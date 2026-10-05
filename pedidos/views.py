from collections import defaultdict
from decimal import Decimal
from io import BytesIO

from django.contrib.auth.decorators import login_required, user_passes_test
from django import forms
from django.db.models import Count, F, Sum
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_POST
from reportlab.lib.pagesizes import A5
from reportlab.pdfgen import canvas
from carta.models import Categoria, Plato
from inventario.models import Insumo
from inventario.models import TasaCambio
from inventario.models import RecetaItem
from usuarios.models import get_turno_abierto, mesas_del_turno
from .models import Mesa, Factura, Pedido


def _build_caja_estadisticas_payload():
    resumen = Factura.objects.aggregate(
        cantidad=Count('id'),
        total=Sum('total_cup'),
        efectivo=Sum('monto_efectivo_cup'),
        transferencia=Sum('monto_transferencia_cup'),
    )
    salidas = defaultdict(lambda: Decimal('0'))
    pedidos_descontados = Pedido.objects.filter(
        inventario_descontado=True,
    ).prefetch_related('items__plato__receta')
    for pedido in pedidos_descontados:
        for item in pedido.items.all():
            for receta in item.plato.receta.all():
                salidas[receta.insumo_id] += receta.cantidad * item.cantidad

    inventario = []
    for insumo in Insumo.objects.all():
        inventario.append({
            'nombre': insumo.nombre,
            'unidad': insumo.get_unidad_display(),
            'salida': str(salidas[insumo.id]),
            'stock_actual': str(insumo.stock_actual),
            'stock_bajo': insumo.stock_bajo,
        })

    return {
        'recaudacion': {
            'cantidad': resumen['cantidad'] or 0,
            'total': str(resumen['total'] or Decimal('0')),
            'efectivo': str(resumen['efectivo'] or Decimal('0')),
            'transferencia': str(resumen['transferencia'] or Decimal('0')),
        },
        'inventario': inventario,
    }


def _rol_permitido(*roles):
    def _check(user):
        return user.is_authenticated and user.role in roles
    return _check

def en_turno(nombre):
    turno = get_turno_abierto()
    return turno.usuarios.filter(username=nombre).exists() if turno is not None else False

@login_required
@user_passes_test(_rol_permitido('mesera'))
def mesera(request):
    productos = Plato.objects.filter(disponible=True).select_related('categoria').order_by('categoria__orden', 'categoria__nombre', 'nombre')
    categorias = Categoria.objects.filter(platos__disponible=True).distinct().order_by('orden', 'nombre')
    turno = get_turno_abierto()
    mesas = mesas_del_turno(turno) if turno else Mesa.objects.none()
    mesera_en_turno = en_turno(request.user.username)
    return render(request, 'mesera/mesera.html', {
        'productos': productos,
        'categorias': categorias,
        'mesas': mesas,
        'turno': turno,
        'en_turno': mesera_en_turno,
    })


@login_required
@user_passes_test(_rol_permitido('mesera'))
@require_POST
def subir_comprobante_transferencia(request):
    uploaded = request.FILES.get('comprobante')
    mesa_numero = request.POST.get('mesa_numero')
    grupo_para_llevar = request.POST.get('grupo_para_llevar')
    if not uploaded or (not mesa_numero and not grupo_para_llevar):
        return JsonResponse({'error': 'Selecciona una foto y una cuenta válida.'}, status=400)
    if uploaded.size > 5 * 1024 * 1024:
        return JsonResponse({'error': 'La foto no puede superar 5 MB.'}, status=400)
    try:
        forms.ImageField().clean(uploaded)
    except ValidationError:
        return JsonResponse({'error': 'El archivo debe ser una imagen válida.'}, status=400)

    if grupo_para_llevar:
        pedidos = Pedido.objects.filter(
            modalidad='para_llevar',
            grupo_para_llevar=grupo_para_llevar,
        ).order_by('pk')
        if not pedidos.exists() or pedidos.exclude(estado='entregado').exists():
            pedido = None
        else:
            pedido = pedidos.first()
    else:
        pedido = Pedido.objects.filter(
            mesa__numero=mesa_numero,
            mesa__abierta=True,
            sesion_id=F('mesa__sesion_id'),
            cuenta_solicitada=True,
        ).exclude(estado='cerrado').order_by('-creado_en').first()
    if not pedido:
        return JsonResponse({'error': 'No hay una cuenta pendiente para esa mesa.'}, status=404)

    pedido.comprobante_transferencia.save(uploaded.name, uploaded, save=True)
    return JsonResponse({'comprobante_url': pedido.comprobante_transferencia.url})

@login_required
@user_passes_test(_rol_permitido('cocina'))
def cocina(request):
    cocina_en_turno = en_turno(request.user.username)
    return render(request, 'pedidos/cocina.html', {'en_turno': cocina_en_turno})


@login_required
@user_passes_test(_rol_permitido('cajera'))
def caja(request):
    return render(request, 'caja/caja.html', {
        'tasa_actual': TasaCambio.actual(),
        'en_turno': en_turno(request.user.username),
    })


@login_required
@user_passes_test(_rol_permitido('cajera'))
def caja_estadisticas_pagina(request):
    payload = _build_caja_estadisticas_payload()
    return render(request, 'caja/estadisticas.html', {
        'recaudacion': payload['recaudacion'],
        'inventario': payload['inventario'],
        'tasa_actual': TasaCambio.actual(),
        'en_turno': en_turno(request.user.username),
    })

def factura_imprimir(request, pk):
    factura = get_object_or_404(Factura, pk=pk)

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A5)
    width, height = A5

    pdf.setTitle(f'Factura #{factura.pk}')
    pdf.setAuthor('Las Cuquis')
    pdf.setFont('Helvetica-Bold', 14)
    pdf.drawString(30, height - 35, 'LAS CUQUIS')
    pdf.setFont('Helvetica', 9)
    pdf.drawString(30, height - 52, 'Factura de consumo')
    pdf.drawString(30, height - 70, f'Factura: #{factura.pk}')
    referencia = (
        factura.pedido.mesa.etiqueta
        if factura.pedido.mesa_id
        else 'Pedido para llevar'
    )
    pdf.drawString(30, height - 82, referencia)
    pdf.drawString(30, height - 94, f'Fecha: {factura.creado_en.strftime("%d/%m/%Y %H:%M")}')
    y_datos = height - 106
    if factura.pedido.modalidad == 'para_llevar':
        pdf.drawString(30, y_datos, f'Cliente: {factura.pedido.nombre_cliente}')
        pdf.drawString(30, y_datos - 12, f'Teléfono: {factura.pedido.telefono_cliente}')
        y_datos -= 24
    if factura.cajera_nombre:
        pdf.drawString(30, y_datos, f'Cajera: {factura.cajera_nombre}')
        y_datos -= 12
    if factura.mesera_nombre:
        pdf.drawString(30, y_datos, f'Mesera: {factura.mesera_nombre}')

    y = min(y_datos - 20, height - 138)
    pdf.setFont('Helvetica-Bold', 9)
    pdf.drawString(30, y, 'ITEM')
    pdf.drawRightString(width - 30, y, 'SUBTOTAL')
    y -= 14
    pdf.setFont('Helvetica', 9)

    for item in factura.items_snapshot or []:
        nombre = f"{item.get('cantidad', 1)}x {item.get('plato', 'Item')}"
        subtotal = item.get('subtotal', '0.00')
        if len(nombre) > 28:
            nombre = nombre[:25] + '...'
        pdf.drawString(30, y, nombre)
        pdf.drawRightString(width - 30, y, f'{subtotal} CUP')
        y -= 16
        if y < 90:
            pdf.showPage()
            y = height - 30

    pdf.setFont('Helvetica-Bold', 10)
    pdf.drawString(30, y - 18, 'TOTAL')
    pdf.drawRightString(width - 30, y - 18, f'{factura.total_cup} CUP')

    pdf.setFont('Helvetica', 9)
    pdf.drawString(30, y - 36, f'Forma de pago: {factura.get_forma_pago_display()}')
    pdf.drawString(30, y - 52, 'Gracias por su visita!')

    pdf.save()

    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="factura_{factura.pk}.pdf"'
    return response


def factura_imprimir_web(request, pk):
    factura = get_object_or_404(Factura, pk=pk)
    return render(request, 'caja/factura_imprimir_web.html', {'factura': factura})

def facturas_historial(request):
    facturas = Factura.objects.select_related('pedido__mesa').order_by('-creado_en')
    return JsonResponse({
        'facturas': [
            {
                'id': factura.id,
                'mesa_numero': factura.mesa_numero,
                'es_para_llevar': factura.pedido.modalidad == 'para_llevar',
                'etiqueta': (
                    factura.pedido.mesa.etiqueta
                    if factura.pedido.mesa_id
                    else f'Para llevar #{factura.pedido.grupo_para_llevar.hex[:8].upper()}'
                    if factura.pedido.grupo_para_llevar
                    else 'Para llevar'
                ),
                'forma_pago_display': factura.get_forma_pago_display(),
                'total_cup': str(factura.total_cup),
                'creado_en': factura.creado_en.strftime('%d/%m/%Y %H:%M'),
            }
            for factura in facturas
        ],
    })

def caja_estadisticas(request):
    return JsonResponse(_build_caja_estadisticas_payload())
