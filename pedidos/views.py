from collections import defaultdict
from decimal import Decimal
from io import BytesIO

from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Count, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, get_object_or_404
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


@login_required
@user_passes_test(_rol_permitido('mesera'))
def mesera(request):
    productos = Plato.objects.filter(disponible=True).select_related('categoria').order_by('categoria__orden', 'categoria__nombre', 'nombre')
    turno = get_turno_abierto()
    mesas = mesas_del_turno(turno) if turno else Mesa.objects.none()
    return render(request, 'mesera/mesera.html', {
        'productos': productos,
        'mesas': mesas,
        'turno': turno,
    })

@login_required
@user_passes_test(_rol_permitido('cocina'))
def cocina(request):
    return render(request, 'pedidos/cocina.html')


@login_required
@user_passes_test(_rol_permitido('cajera'))
def caja(request):
    return render(request, 'caja/caja.html', {
        'tasa_actual': TasaCambio.actual(),
    })


@login_required
@user_passes_test(_rol_permitido('cajera'))
def caja_estadisticas_pagina(request):
    payload = _build_caja_estadisticas_payload()
    return render(request, 'caja/estadisticas.html', {
        'recaudacion': payload['recaudacion'],
        'inventario': payload['inventario'],
        'tasa_actual': TasaCambio.actual(),
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
    pdf.drawString(30, height - 82, f'Mesa: {factura.mesa_numero}')
    pdf.drawString(30, height - 94, f'Fecha: {factura.creado_en.strftime("%d/%m/%Y %H:%M")}')
    if factura.cajera_nombre:
        pdf.drawString(30, height - 106, f'Cajera: {factura.cajera_nombre}')
    if factura.mesera_nombre:
        pdf.drawString(30, height - 118, f'Mesera: {factura.mesera_nombre}')

    y = height - 138
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
    facturas = Factura.objects.order_by('-creado_en')
    return JsonResponse({
        'facturas': [
            {
                'id': factura.id,
                'mesa_numero': factura.mesa_numero,
                'forma_pago_display': factura.get_forma_pago_display(),
                'total_cup': str(factura.total_cup),
                'creado_en': factura.creado_en.strftime('%d/%m/%Y %H:%M'),
            }
            for factura in facturas
        ],
    })

def caja_estadisticas(request):
    return JsonResponse(_build_caja_estadisticas_payload())
