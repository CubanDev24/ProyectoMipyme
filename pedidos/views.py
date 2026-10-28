from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal
from html import escape as escape_html
from io import BytesIO

from django.contrib.auth.decorators import login_required, user_passes_test
from django import forms
from django.core.paginator import Paginator
from django.db.models import Count, F, Sum
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_POST
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import HRFlowable, Paragraph, Spacer, Table, TableStyle
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
    for insumo in Insumo.objects.select_related('categoria_inventario'):
        inventario.append({
            'nombre': insumo.nombre,
            'area': insumo.get_tipo_inventario_display() if insumo.tipo_inventario else 'Por clasificar',
            'categoria': insumo.categoria_inventario.nombre if insumo.categoria_inventario else 'Sin categoría',
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
    productos = Plato.objects.filter(
        disponible=True,
        receta__isnull=False,
    ).exclude(
        receta__insumo__stock_actual__lte=0,
    ).distinct().select_related('categoria').order_by('categoria__orden', 'categoria__nombre', 'nombre')
    categorias = Categoria.objects.filter(
        platos__in=productos,
    ).distinct().order_by('orden', 'nombre')
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
            estado__in=['pendiente', 'en_preparacion', 'listo', 'servido'],
        ).order_by('-creado_en').first()
    if not pedido:
        return JsonResponse({'error': 'No hay una cuenta pendiente para esa mesa.'}, status=404)

    pedido.comprobante_transferencia.save(uploaded.name, uploaded, save=True)
    return JsonResponse({'comprobante_url': pedido.comprobante_transferencia.url})

@login_required
@user_passes_test(_rol_permitido('cocina'))
def cocina(request):
    cocina_en_turno = en_turno(request.user.username)
    insumos_cocina = Insumo.objects.filter(
        tipo_inventario='cocina',
    ).select_related('categoria_inventario').order_by('nombre')
    return render(request, 'pedidos/cocina.html', {
        'en_turno': cocina_en_turno,
        'insumos_cocina': insumos_cocina,
    })


@login_required
@user_passes_test(_rol_permitido('cocina'))
def cocina_ventas_hoy(request):
    hoy = timezone.localdate()
    inicio = timezone.make_aware(datetime.combine(hoy, time.min))
    fin = inicio + timedelta(days=1)
    ventas = {}
    snapshots = Factura.objects.filter(
        creado_en__gte=inicio,
        creado_en__lt=fin,
    ).values_list('items_snapshot', flat=True)

    for snapshot in snapshots:
        for item in snapshot or []:
            plato = str(item.get('plato', '')).strip()
            if not plato:
                continue
            try:
                cantidad = int(item.get('cantidad', 0))
                plato_id = int(item['plato_id']) if item.get('plato_id') is not None else None
            except (TypeError, ValueError):
                continue
            if cantidad <= 0:
                continue
            clave = ('id', plato_id) if plato_id is not None else ('nombre', plato.casefold())
            venta = ventas.setdefault(clave, {'plato': plato, 'cantidad': 0})
            venta['cantidad'] += cantidad

    ventas_por_plato_id = {
        clave[1]: venta
        for clave, venta in ventas.items()
        if clave[0] == 'id' and clave[1] is not None
    }
    for plato in Plato.objects.filter(pk__in=ventas_por_plato_id).only('id', 'imagen'):
        ventas_por_plato_id[plato.pk]['imagen_url'] = plato.imagen.url if plato.imagen else None

    recetas = RecetaItem.objects.filter(
        plato_id__in=ventas_por_plato_id,
    ).select_related('insumo').order_by('plato_id', 'insumo__nombre')
    for receta in recetas:
        venta = ventas_por_plato_id[receta.plato_id]
        venta.setdefault('ingredientes', []).append({
            'insumo': receta.insumo.nombre,
            'cantidad_por_plato': format(receta.cantidad.normalize(), 'f'),
            'cantidad_total': format(
                (receta.cantidad * venta['cantidad']).normalize(),
                'f',
            ),
            'unidad': receta.insumo.get_unidad_display().lower(),
        })

    for venta in ventas.values():
        venta.setdefault('ingredientes', [])
        venta.setdefault('imagen_url', None)

    return JsonResponse({
        'fecha': hoy.strftime('%d/%m/%Y'),
        'actualizado_en': timezone.localtime().strftime('%H:%M:%S'),
        'ventas': sorted(ventas.values(), key=lambda venta: venta['plato'].casefold()),
    })


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

    ancho_papel = 58 * mm
    margen_izquierdo = 2.5 * mm
    margen_derecho = 3 * mm
    ancho_contenido = ancho_papel - margen_izquierdo - margen_derecho
    estilos = {
        'titulo': ParagraphStyle(
            'TicketTitulo', fontName='Helvetica-Bold', fontSize=18, leading=20,
            alignment=TA_CENTER, textColor=colors.black,
        ),
        'subtitulo': ParagraphStyle(
            'TicketSubtitulo', fontName='Helvetica-Bold', fontSize=8.5, leading=11,
            alignment=TA_CENTER, textColor=colors.black,
        ),
        'direccion': ParagraphStyle(
            'TicketDireccion', fontName='Helvetica-Bold', fontSize=8.5, leading=11,
            alignment=TA_CENTER, textColor=colors.black,
        ),
        'etiqueta': ParagraphStyle(
            'TicketEtiqueta', fontName='Helvetica-Bold', fontSize=7.5, leading=9,
            textColor=colors.black,
        ),
        'valor': ParagraphStyle(
            'TicketValor', fontName='Helvetica-Bold', fontSize=9.5, leading=11,
            alignment=TA_RIGHT, textColor=colors.black, wordWrap='CJK',
        ),
        'seccion': ParagraphStyle(
            'TicketSeccion', fontName='Helvetica-Bold', fontSize=7.5, leading=9,
            textColor=colors.black,
        ),
        'cantidad': ParagraphStyle(
            'TicketCantidad', fontName='Helvetica-Bold', fontSize=9.5, leading=11,
            textColor=colors.black,
        ),
        'producto': ParagraphStyle(
            'TicketProducto', fontName='Helvetica-Bold', fontSize=9.5, leading=11.5,
            textColor=colors.black, wordWrap='CJK',
        ),
        'importe': ParagraphStyle(
            'TicketImporte', fontName='Helvetica-Bold', fontSize=9, leading=11,
            alignment=TA_RIGHT, textColor=colors.black,
        ),
        'total_etiqueta': ParagraphStyle(
            'TicketTotalEtiqueta', fontName='Helvetica-Bold', fontSize=10, leading=13,
            textColor=colors.white,
        ),
        'total_valor': ParagraphStyle(
            'TicketTotalValor', fontName='Helvetica-Bold', fontSize=14, leading=16,
            alignment=TA_RIGHT, textColor=colors.white,
        ),
        'pago': ParagraphStyle(
            'TicketPago', fontName='Helvetica-Bold', fontSize=8, leading=10,
            alignment=TA_CENTER, textColor=colors.black,
        ),
        'pie': ParagraphStyle(
            'TicketPie', fontName='Helvetica-Bold', fontSize=8.5, leading=11,
            alignment=TA_CENTER, textColor=colors.black,
        ),
        'codigo': ParagraphStyle(
            'TicketCodigo', fontName='Helvetica-Bold', fontSize=10.5, leading=13,
            alignment=TA_CENTER, textColor=colors.black,
        ),
    }

    def parrafo(texto, estilo, permitir_html=False):
        contenido = str(texto) if permitir_html else escape_html(str(texto))
        return Paragraph(contenido, estilo)

    historia = [
        parrafo('LAS CUQUIS', estilos['titulo']),
        Spacer(1, 2),
        parrafo('FACTURA DE CONSUMO', estilos['subtitulo']),
        Spacer(1, 5),
        parrafo('VISÍTANOS<br/>Calle 100 entre 117 y 123<br/>Reparto Puente Nuevo, Marianao', estilos['direccion'], permitir_html=True),
        Spacer(1, 7),
        HRFlowable(width='100%', thickness=0.6, color=colors.black),
        Spacer(1, 5),
    ]

    def agregar_dato(etiqueta, valor):
        fila = Table(
            [[parrafo(etiqueta.upper(), estilos['etiqueta']), parrafo(valor, estilos['valor'])]],
            colWidths=[18 * mm, ancho_contenido - 18 * mm],
        )
        fila.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 1.5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5),
        ]))
        historia.append(fila)

    if factura.pedido.modalidad == 'para_llevar':
        historia.extend([parrafo('PARA LLEVAR', estilos['subtitulo']), Spacer(1, 4)])
    agregar_dato('Factura', f'#{factura.pk}')
    referencia = factura.pedido.mesa.etiqueta if factura.pedido.mesa_id else 'Para llevar'
    agregar_dato('Mesa', referencia)
    agregar_dato('Fecha', factura.creado_en.strftime('%d/%m/%Y · %H:%M'))
    if factura.pedido.modalidad == 'para_llevar':
        agregar_dato('Cliente', factura.pedido.nombre_cliente or 'Sin nombre')
        agregar_dato('Teléfono', factura.pedido.telefono_cliente or 'Sin teléfono')
    if factura.cajera_nombre:
        agregar_dato('Cajera', factura.cajera_nombre)
    if factura.mesera_nombre:
        agregar_dato('Mesera', factura.mesera_nombre)

    historia.extend([
        Spacer(1, 5),
        HRFlowable(width='100%', thickness=0.5, color=colors.black),
        Spacer(1, 4),
    ])
    encabezado_detalle = Table(
        [[parrafo('DETALLE', estilos['seccion']), '', parrafo('IMPORTE', estilos['seccion'])]],
        colWidths=[8 * mm, ancho_contenido - 8 * mm - 23 * mm, 23 * mm],
    )
    encabezado_detalle.setStyle(TableStyle([
        ('ALIGN', (2, 0), (2, 0), 'RIGHT'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    historia.append(encabezado_detalle)

    anchos_items = [8 * mm, ancho_contenido - 8 * mm - 23 * mm, 23 * mm]
    for item in factura.items_snapshot or []:
        nombre_producto = escape_html(str(item.get('plato', 'Item')))
        if item.get('cantidad', 1) > 1 and item.get('precio_unit'):
            nombre_producto += (
                f"<br/><font size='7.5'>{item['cantidad']} × "
                f"{escape_html(str(item['precio_unit']))} CUP</font>"
            )
        fila_item = Table(
            [[
                parrafo(f"{item.get('cantidad', 1)}x", estilos['cantidad']),
                parrafo(nombre_producto, estilos['producto'], permitir_html=True),
                parrafo(f"{item.get('subtotal', '0.00')} CUP", estilos['importe']),
            ]],
            colWidths=anchos_items,
        )
        fila_item.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (2, 0), (2, 0), 'RIGHT'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ]))
        historia.append(fila_item)

    historia.extend([
        Spacer(1, 5),
        HRFlowable(width='100%', thickness=0.5, color=colors.black),
        Spacer(1, 5),
    ])
    tabla_total = Table(
        [[
            parrafo('TOTAL', estilos['total_etiqueta']),
            parrafo(f'{escape_html(str(factura.total_cup))} <font size="8">CUP</font>', estilos['total_valor'], permitir_html=True),
        ]],
        colWidths=[ancho_contenido * 0.36, ancho_contenido * 0.64],
    )
    tabla_total.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    historia.extend([tabla_total, Spacer(1, 5), HRFlowable(width='100%', thickness=0.5, color=colors.black), Spacer(1, 4)])

    pago = Table([[parrafo(factura.get_forma_pago_display().upper(), estilos['pago'])]], colWidths=[ancho_contenido])
    pago.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, colors.black),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    historia.extend([pago, Spacer(1, 4)])
    if factura.forma_pago == 'efectivo_cup':
        agregar_dato('Efectivo CUP', factura.monto_efectivo_cup)
        if factura.monto_recibido_cup:
            agregar_dato('Recibido', f'{factura.monto_recibido_cup} CUP')
            agregar_dato('Cambio', f'{factura.cambio_cup} CUP')
    elif factura.forma_pago == 'transferencia':
        agregar_dato('Transferencia', factura.monto_transferencia_cup)
    elif factura.forma_pago == 'mixto':
        agregar_dato('Efectivo CUP', factura.monto_efectivo_cup)
        agregar_dato('Transferencia', factura.monto_transferencia_cup)
    elif factura.forma_pago == 'usd':
        agregar_dato('Monto USD', f'{factura.monto_usd} USD')
        agregar_dato('Tasa', f'1 USD = {factura.tasa_cambio} CUP')
        if factura.monto_recibido_usd:
            agregar_dato('Recibido', f'{factura.monto_recibido_usd} USD')
            agregar_dato('Cambio', f'{factura.cambio_usd} USD')
    if factura.monto_usd and factura.forma_pago != 'usd':
        agregar_dato('Total USD', f'{factura.monto_usd} USD')

    historia.extend([
        Spacer(1, 6),
        HRFlowable(width='100%', thickness=0.6, color=colors.black),
        Spacer(1, 5),
        parrafo('El ingrediente secreto siempre es tu visita.', estilos['pie']),
        Spacer(1, 3),
        parrafo('¡Vuelve pronto!', estilos['titulo']),
        Spacer(1, 5),
        HRFlowable(width='100%', thickness=0.5, color=colors.black),
        Spacer(1, 3),
        parrafo(f'{factura.pk:06d}', estilos['codigo']),
        Spacer(1, 5),
        parrafo('GRACIAS POR SU COMPRA', estilos['pie']),
    ])

    buffer = BytesIO()
    altura_superior = 3 * mm
    altura_inferior = 14 * mm
    altura_contenido = sum(flowable.wrap(ancho_contenido, 100000)[1] for flowable in historia)
    altura_pagina = altura_superior + altura_contenido + altura_inferior
    pdf = canvas.Canvas(buffer, pagesize=(ancho_papel, altura_pagina))
    pdf.setTitle(f'Factura #{factura.pk}')
    pdf.setAuthor('Las Cuquis')
    cursor_y = altura_pagina - altura_superior
    for flowable in historia:
        _, altura = flowable.wrap(ancho_contenido, cursor_y)
        cursor_y -= altura
        flowable.drawOn(pdf, margen_izquierdo, cursor_y)

    pdf.save()

    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    disposicion = 'inline' if request.GET.get('inline') == '1' else 'attachment'
    response['Content-Disposition'] = f'{disposicion}; filename="factura_{factura.pk}.pdf"'
    return response


def factura_imprimir_web(request, pk):
    factura = get_object_or_404(Factura, pk=pk)
    return render(request, 'caja/factura_imprimir_web.html', {'factura': factura})

def facturas_historial(request):
    turno = get_turno_abierto()
    facturas = turno.facturas_queryset if turno else Factura.objects.none()
    pagina = Paginator(
        facturas.select_related('pedido__mesa').order_by('-creado_en', '-pk'),
        10,
    ).get_page(request.GET.get('page'))
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
            for factura in pagina
        ],
        'pagina': pagina.number,
        'paginas': pagina.paginator.num_pages,
        'total': pagina.paginator.count,
        'anterior': pagina.has_previous(),
        'siguiente': pagina.has_next(),
    })

def caja_estadisticas(request):
    return JsonResponse(_build_caja_estadisticas_payload())
