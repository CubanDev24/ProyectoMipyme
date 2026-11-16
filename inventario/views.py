import json
import unicodedata
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Prefetch, Q, Sum
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_POST

from carta.models import Categoria, Plato
from carta.qr import generar_qr_sistema, generar_qr_wifi
from usuarios.admin_layout import admin_page_context
from .models import CategoriaInventario, Guarnicion, GuarnicionRecetaItem, Insumo, MovimientoInventario, RecetaItem, TasaCambio
from .services import receta_tiene_insumo_sin_stock
from usuarios.models import ConfiguracionWifi


def _es_admin(user):
    return user.is_authenticated and user.role == 'administrador'


def _es_admin_o_cajera(user):
    return user.is_authenticated and user.role in ('administrador', 'cajera')


def _es_cajera(user):
    return user.is_authenticated and user.role == 'cajera'


def _url_gestion_carta(user):
    if user.role == 'cajera':
        return reverse('inventario:carta_cajera')
    return f'{reverse("inventario:carta_admin")}#carta-platos'


def _url_admin_seccion(seccion, ancla=''):
    rutas = {
        'configuracion': 'inventario:configuracion_admin',
        'restaurante': 'inventario:restaurante_admin',
        'cocina': 'inventario:cocina_admin',
        'carta': 'inventario:carta_admin',
    }
    return f'{reverse(rutas[seccion])}{ancla}'


def _url_inventario_admin(tipo):
    if tipo in ('cocina', 'restaurante'):
        return _url_admin_seccion(tipo)
    return _url_admin_seccion('configuracion')


def _icono_categoria(nombre):
    nombre_normalizado = unicodedata.normalize('NFKD', nombre)
    nombre_normalizado = ''.join(
        caracter for caracter in nombre_normalizado
        if not unicodedata.combining(caracter)
    ).lower()
    if 'entrante' in nombre_normalizado or 'entrada' in nombre_normalizado:
        return 'fa-utensils'
    if 'sopa' in nombre_normalizado:
        return 'fa-bowl-food'
    if 'principal' in nombre_normalizado or 'carne' in nombre_normalizado:
        return 'fa-drumstick-bite'
    if 'postre' in nombre_normalizado:
        return 'fa-ice-cream'
    if 'bebida' in nombre_normalizado:
        return 'fa-martini-glass-citrus'
    return 'fa-tag'


def _icono_categoria_inventario(nombre, tipo='cocina'):
    nombre_normalizado = unicodedata.normalize('NFKD', nombre)
    nombre_normalizado = ''.join(
        caracter for caracter in nombre_normalizado
        if not unicodedata.combining(caracter)
    ).lower()
    if tipo == 'restaurante':
        if 'vino' in nombre_normalizado:
            return 'fa-wine-glass'
        if any(palabra in nombre_normalizado for palabra in ('licor', 'ron', 'whisky', 'vodka', 'gin', 'tequila', 'destil')):
            return 'fa-wine-bottle'
        if 'cerve' in nombre_normalizado:
            return 'fa-beer-mug-empty'
        if any(palabra in nombre_normalizado for palabra in ('refresc', 'jugo', 'batido', 'bebid')):
            return 'fa-glass-water'
        if any(palabra in nombre_normalizado for palabra in ('agua', 'hielo')):
            return 'fa-bottle-water'
        if any(palabra in nombre_normalizado for palabra in ('barra', 'insumo', 'descart')):
            return 'fa-box-open'
    if any(palabra in nombre_normalizado for palabra in ('prote', 'carne', 'pollo', 'pescado')):
        return 'fa-drumstick-bite'
    if any(palabra in nombre_normalizado for palabra in ('despens', 'grano', 'seco')):
        return 'fa-box-open'
    if any(palabra in nombre_normalizado for palabra in ('fruta', 'verdur', 'vegetal')):
        return 'fa-carrot'
    if any(palabra in nombre_normalizado for palabra in ('lacte', 'huevo', 'queso')):
        return 'fa-egg'
    if any(palabra in nombre_normalizado for palabra in ('bebid', 'licor')):
        return 'fa-bottle-water'
    return 'fa-tag'


def _platos_por_categoria(platos, categorias):
    grupos = []
    for categoria in categorias:
        platos_categoria = [
            plato for plato in platos if plato.categoria_id == categoria.pk
        ]
        if platos_categoria:
            grupos.append({
                'id': categoria.pk,
                'nombre': categoria.nombre,
                'platos': platos_categoria,
            })
    platos_sin_categoria = [
        plato for plato in platos if plato.categoria_id is None
    ]
    if platos_sin_categoria:
        grupos.append({
            'id': '',
            'nombre': 'Sin categoría',
            'platos': platos_sin_categoria,
        })
    return grupos


def _to_decimal(valor, default=None):
    try:
        decimal = Decimal(str(valor).replace(',', '.').strip())
        return decimal if decimal.is_finite() else default
    except (InvalidOperation, ValueError, AttributeError):
        return default


def _decimal_fits_field(valor, max_digits, decimal_places, allow_negative=False):
    if valor is None or (valor < 0 and not allow_negative):
        return False
    try:
        cuantizado = valor.quantize(Decimal(1).scaleb(-decimal_places))
    except InvalidOperation:
        return False
    limite = Decimal(1).scaleb(max_digits - decimal_places)
    return valor == cuantizado and abs(valor) < limite


def _pagination_query(request, page_parameter):
    query = request.GET.copy()
    query.pop(page_parameter, None)
    return query.urlencode()


def _receta_desde_post(request, insumo_field='insumo_id[]', cantidad_field='cantidad[]'):
    insumo_ids = request.POST.getlist(insumo_field) or request.POST.getlist(insumo_field.removesuffix('[]'))
    cantidades = request.POST.getlist(cantidad_field) or request.POST.getlist(cantidad_field.removesuffix('[]'))
    if len(insumo_ids) != len(cantidades):
        return None, 'Cada producto de la receta necesita una cantidad.'

    receta = []
    insumos_vistos = set()
    for insumo_id, cantidad_raw in zip(insumo_ids, cantidades):
        insumo_id = (insumo_id or '').strip()
        cantidad_raw = (cantidad_raw or '').strip()
        if not insumo_id and not cantidad_raw:
            continue
        if not insumo_id:
            return None, 'Selecciona un producto para cada cantidad indicada.'
        cantidad = _to_decimal(cantidad_raw)
        if cantidad is None or cantidad <= 0 or cantidad.as_tuple().exponent < -3:
            return None, 'Las cantidades de receta deben ser positivas y tener hasta tres decimales.'
        if insumo_id in insumos_vistos:
            return None, 'No repitas un producto en la receta; suma sus cantidades en una sola fila.'
        insumo = Insumo.objects.filter(
            pk=insumo_id,
            activo=True,
            tipo_inventario__in=['cocina', 'restaurante'],
        ).first()
        if not insumo:
            return None, 'Selecciona productos activos de Cocina o Restaurante.'
        receta.append((insumo, cantidad))
        insumos_vistos.add(insumo_id)
    return receta, None


def _guarniciones_desde_post(request):
    if request.POST.get('trae_guarniciones_incluidas') != 'on':
        return [], None
    ids = request.POST.getlist('guarnicion_ids')
    if not ids:
        return None, 'Selecciona al menos una guarnición incluida para este plato.'
    if len(ids) != len(set(ids)):
        return None, 'No repitas guarniciones en el mismo plato.'
    guarniciones = list(Guarnicion.objects.filter(
        pk__in=ids,
        activa=True,
    ).prefetch_related('receta__insumo'))
    if len(guarniciones) != len(ids):
        return None, 'Una o más guarniciones ya no están activas.'
    for guarnicion in guarniciones:
        receta = [(item.insumo, item.cantidad) for item in guarnicion.receta.all()]
        if not receta:
            return None, f'La guarnición {guarnicion.nombre} necesita una receta de inventario.'
        if receta_tiene_insumo_sin_stock(receta):
            return None, f'La guarnición {guarnicion.nombre} tiene productos sin stock.'
    return guarniciones, None


@login_required
@user_passes_test(_es_admin)
def _render_administrador(request, admin_pagina=None):
    insumos = Insumo.objects.select_related('categoria', 'categoria_inventario').all()
    movimientos = Paginator(
        MovimientoInventario.objects.select_related('insumo', 'usuario').order_by('-creado_en', '-pk'),
        10,
    ).get_page(request.GET.get('movimientos_page'))
    movimientos_pagina_query = _pagination_query(request, 'movimientos_page')
    platos = list(Plato.objects.select_related('categoria').prefetch_related(
        Prefetch(
            'receta',
            queryset=RecetaItem.objects.select_related('insumo'),
            to_attr='receta_admin',
        )
    ))
    for plato in platos:
        plato.receta_json = json.dumps([
            {'insumo_id': item.insumo_id, 'cantidad': str(item.cantidad)}
            for item in plato.receta_admin
        ])
        plato.receta_con_stock_bajo = any(
            item.insumo.stock_bajo for item in plato.receta_admin
        )
        plato.guarniciones_ids = list(plato.guarniciones_incluidas.values_list('pk', flat=True))
        plato.guarniciones_json = json.dumps(plato.guarniciones_ids)
    categorias = list(Categoria.objects.annotate(
        platos_count=Count('platos', distinct=True),
    ).prefetch_related(
        Prefetch(
            'platos',
            queryset=Plato.objects.filter(
                disponible=True,
                receta__isnull=False,
            ).distinct(),
            to_attr='platos_publicados',
        )
    ))
    total_platos_categorias = sum(categoria.platos_count for categoria in categorias)
    for indice, categoria in enumerate(categorias):
        categoria.icono_admin = _icono_categoria(categoria.nombre)
        categoria.puede_subir = indice > 0
        categoria.puede_bajar = indice < len(categorias) - 1
        categoria.porcentaje_platos = (
            int(categoria.platos_count * 100 / total_platos_categorias + 0.5)
            if total_platos_categorias
            else 0
        )
    platos_por_categoria = _platos_por_categoria(platos, categorias)
    platos_sin_categoria = [
        plato for plato in platos
        if plato.categoria_id is None and plato.disponible and plato.receta_admin
    ]
    guarniciones = list(Guarnicion.objects.annotate(
        platos_count=Count('platos', distinct=True),
    ).prefetch_related(
        Prefetch('receta', queryset=GuarnicionRecetaItem.objects.select_related('insumo'), to_attr='receta_admin'),
    ))
    for guarnicion in guarniciones:
        guarnicion.receta_json = json.dumps([
            {'insumo_id': item.insumo_id, 'cantidad': str(item.cantidad)}
            for item in guarnicion.receta_admin
        ])
    guarniciones_activas = [
        item for item in guarniciones
        if item.activa and item.receta_admin and all(
            receta.insumo.activo and receta.insumo.stock_actual > 0
            for receta in item.receta_admin
        )
    ]
    platos_publicados = [
        plato for plato in platos
        if plato.disponible and plato.receta_admin
    ]

    plato_sel = None
    plato_id = request.GET.get('plato')
    if plato_id:
        plato_sel = Plato.objects.filter(pk=plato_id).first()
    receta_items = []
    if plato_sel:
        receta_items = RecetaItem.objects.filter(plato=plato_sel).select_related('insumo')
    inventarios = []
    for tipo, nombre in CategoriaInventario.TIPO_CHOICES:
        page_parameter = f'{tipo}_page'
        inventarios.append({
            'tipo': tipo,
            'nombre': nombre,
            'insumos': Paginator(
                insumos.filter(tipo_inventario=tipo).order_by('nombre'),
                25,
            ).get_page(request.GET.get(page_parameter)),
            'page_parameter': page_parameter,
            'pagination_query': _pagination_query(request, page_parameter),
            'pagination_anchor': f'#inventario-{tipo}',
            'categorias': CategoriaInventario.objects.filter(tipo=tipo),
            'categoria_carta': categorias,
        })
    inventario_admin = next(
        (area for area in inventarios if area['tipo'] == admin_pagina),
        None,
    )
    inventario_kpis = {}
    if admin_pagina in ('cocina', 'restaurante') and inventario_admin:
        tipo_inventario = admin_pagina
        insumos_area = insumos.filter(tipo_inventario=tipo_inventario)
        inventario_kpis = insumos_area.aggregate(
            productos=Count('pk'),
            valor=Sum(ExpressionWrapper(
                F('stock_actual') * F('costo'),
                output_field=DecimalField(max_digits=20, decimal_places=5),
            )),
            bajos=Count(
                'pk',
                filter=Q(stock_actual__gt=0, stock_actual__lte=F('stock_minimo')),
            ),
            agotados=Count('pk', filter=Q(stock_actual__lte=0)),
        )
        inventario_kpis['valor'] = inventario_kpis['valor'] or Decimal('0')
        categorias_area = list(
            CategoriaInventario.objects.filter(tipo=tipo_inventario).annotate(
                productos_count=Count(
                    'insumos',
                    filter=Q(insumos__tipo_inventario=tipo_inventario),
                    distinct=True,
                ),
                productos_bajos=Count(
                    'insumos',
                    filter=Q(
                        insumos__tipo_inventario=tipo_inventario,
                        insumos__stock_actual__lte=F('insumos__stock_minimo'),
                    ),
                    distinct=True,
                ),
            )
        )
        for categoria in categorias_area:
            categoria.icono_admin = _icono_categoria_inventario(
                categoria.nombre,
                tipo_inventario,
            )
            categoria.porcentaje_admin = (
                int(categoria.productos_count * 100 / inventario_kpis['productos'] + 0.5)
                if inventario_kpis['productos']
                else 0
            )
        inventario_admin = dict(inventario_admin, categorias=categorias_area)
        for insumo in inventario_admin['insumos']:
            if insumo.stock_actual <= 0:
                estado, etiqueta = 'zero', 'Agotado'
            elif insumo.stock_bajo:
                estado, etiqueta = 'low', 'Bajo mínimo'
            elif insumo.stock_maximo > 0 and insumo.stock_actual > insumo.stock_maximo:
                estado, etiqueta = 'over', 'Excedido'
            else:
                estado, etiqueta = 'ok', 'Normal'
            insumo.estado_admin = estado
            insumo.estado_admin_label = etiqueta
            insumo.porcentaje_stock = (
                min(int(insumo.stock_actual * 100 / insumo.stock_maximo + Decimal('0.5')), 100)
                if insumo.stock_maximo > 0
                else 0
            )
    insumos_por_clasificar = Paginator(
        insumos.filter(tipo_inventario__isnull=True).order_by('nombre'),
        25,
    ).get_page(request.GET.get('sin_clasificar_page'))
    context = {
        'admin_pagina': admin_pagina,
        'insumos': insumos,
        'inventarios': inventarios,
        'inventario_admin': inventario_admin,
        'inventario_kpis': inventario_kpis,
        'unidad_choices': Insumo.UNIDAD_CHOICES,
        'insumos_por_clasificar': insumos_por_clasificar,
        'sin_clasificar_pagination_query': _pagination_query(request, 'sin_clasificar_page'),
        'sin_clasificar_pagination_anchor': '#inventario-por-clasificar',
        'categorias_inventario': CategoriaInventario.objects.all(),
        'movimientos': movimientos,
        'ultimo_movimiento': (
            MovimientoInventario.objects.order_by('-creado_en', '-pk').first()
            if admin_pagina == 'configuracion'
            else None
        ),
        'movimientos_pagination_query': movimientos_pagina_query,
        'movimientos_pagination_anchor': '#movimientos-inventario',
        'platos': platos,
        'platos_por_categoria': platos_por_categoria,
        'guarniciones': guarniciones,
        'guarniciones_activas': guarniciones_activas,
        'platos_disponibles_count': sum(plato.disponible for plato in platos),
        'platos_no_disponibles_count': sum(not plato.disponible for plato in platos),
        'carta_publicada_count': len(platos_publicados) + len(guarniciones_activas),
        'categorias': categorias,
        'categorias_visibles_count': sum(categoria.visible for categoria in categorias),
        'categorias_ocultas_count': sum(not categoria.visible for categoria in categorias),
        'platos_categorias_count': total_platos_categorias,
        'platos_sin_categoria': platos_sin_categoria,
        'plato_sel': plato_sel,
        'receta_items': receta_items,
        'insumos_disponibles': Insumo.objects.filter(
            activo=True,
            tipo_inventario__in=['cocina', 'restaurante'],
        ).select_related('categoria_inventario').order_by('tipo_inventario', 'nombre'),
        'tasa_actual': TasaCambio.actual(),
    }
    context.update(admin_page_context(admin_pagina or 'admin'))
    return render(request, 'administrador/base_admin.html', context)


@login_required
@user_passes_test(_es_admin)
def administrador(request):
    return _render_administrador(request)


@login_required
@user_passes_test(_es_admin)
def configuracion_admin(request):
    return _render_administrador(request, 'configuracion')


@login_required
@user_passes_test(_es_admin)
def restaurante_admin(request):
    return _render_administrador(request, 'restaurante')


@login_required
@user_passes_test(_es_admin)
def cocina_admin(request):
    return _render_administrador(request, 'cocina')


@login_required
@user_passes_test(_es_admin)
def carta_admin(request):
    return _render_administrador(request, 'carta')


@login_required
@user_passes_test(_es_admin)
def wifi_qr(request):
    wifi_configuracion = ConfiguracionWifi.objects.filter(pk=1).first()
    wifi_qr = (
        generar_qr_wifi(wifi_configuracion.ssid, wifi_configuracion.password)
        if wifi_configuracion and wifi_configuracion.ssid
        else None
    )
    sistema_url, sistema_qr = generar_qr_sistema(request)
    context = {
        'wifi_configuracion': wifi_configuracion,
        'wifi_qr': wifi_qr,
        'sistema_url': sistema_url,
        'sistema_qr': sistema_qr,
    }
    context.update(admin_page_context('wifi'))
    return render(request, 'administrador/base_admin.html', context)


@login_required
@user_passes_test(_es_cajera)
def carta_cajera(request):
    platos = list(Plato.objects.select_related('categoria').prefetch_related(
        Prefetch(
            'receta',
            queryset=RecetaItem.objects.select_related('insumo'),
            to_attr='receta_admin',
        )
    ))
    for plato in platos:
        plato.receta_json = json.dumps([
            {'insumo_id': item.insumo_id, 'cantidad': str(item.cantidad)}
            for item in plato.receta_admin
        ])
        plato.receta_con_stock_bajo = any(
            item.insumo.stock_bajo for item in plato.receta_admin
        )

    categorias = Categoria.objects.prefetch_related(
        Prefetch(
            'platos',
            queryset=Plato.objects.filter(
                disponible=True,
                receta__isnull=False,
            ).distinct(),
            to_attr='platos_publicados',
        )
    )
    platos_por_categoria = _platos_por_categoria(platos, categorias)
    return render(request, 'administrador/administrador.html', {
        'platos': platos,
        'platos_por_categoria': platos_por_categoria,
        'categorias': categorias,
        'insumos_disponibles': Insumo.objects.filter(
            activo=True,
            tipo_inventario__in=['cocina', 'restaurante'],
        ).select_related('categoria_inventario').order_by('nombre'),
        'vista_cajera': True,
    })


@login_required
@user_passes_test(_es_cajera)
def entrada_almacen_cajera(request):
    inventarios = []
    for tipo, nombre in CategoriaInventario.TIPO_CHOICES:
        producto_filtro = request.GET.get(f'{tipo}_producto', '').strip()
        categoria_filtro = request.GET.get(f'{tipo}_categoria', '').strip()
        categorias = list(CategoriaInventario.objects.filter(tipo=tipo).order_by('nombre'))
        categoria_ids = {str(categoria.pk) for categoria in categorias}
        if categoria_filtro not in categoria_ids:
            categoria_filtro = ''

        insumos = Insumo.objects.filter(
            activo=True,
            tipo_inventario=tipo,
        ).select_related('categoria_inventario')
        if producto_filtro:
            insumos = insumos.filter(nombre__icontains=producto_filtro)
        if categoria_filtro:
            insumos = insumos.filter(categoria_inventario_id=int(categoria_filtro))
        page_parameter = f'{tipo}_page'
        insumos_paginados = Paginator(
            insumos.order_by('nombre', 'pk'),
            15,
        ).get_page(request.GET.get(page_parameter))

        otro_tipo = 'restaurante' if tipo == 'cocina' else 'cocina'
        filtros_preservados = [
            {
                'nombre': f'{otro_tipo}_{campo}',
                'valor': request.GET.get(f'{otro_tipo}_{campo}', ''),
            }
            for campo in ('producto', 'categoria', 'page')
            if request.GET.get(f'{otro_tipo}_{campo}', '')
        ]
        query_sin_filtros_area = request.GET.copy()
        for parametro in (
            f'{tipo}_producto',
            f'{tipo}_categoria',
            page_parameter,
        ):
            query_sin_filtros_area.pop(parametro, None)
        query_sin_filtros_area = query_sin_filtros_area.urlencode()
        limpiar_url = (
            f'?{query_sin_filtros_area}#inventario-{tipo}'
            if query_sin_filtros_area
            else f'#inventario-{tipo}'
        )

        inventarios.append({
            'tipo': tipo,
            'nombre': nombre,
            'insumos': insumos_paginados,
            'categorias': categorias,
            'producto_filtro': producto_filtro,
            'categoria_filtro': categoria_filtro,
            'page_parameter': page_parameter,
            'pagination_query': _pagination_query(request, page_parameter),
            'pagination_anchor': f'#inventario-{tipo}',
            'filtros_preservados': filtros_preservados,
            'limpiar_url': limpiar_url,
        })

    if request.method == 'POST':
        insumo_id = request.POST.get('insumo_id', '').strip()
        cantidad = _to_decimal(request.POST.get('cantidad'))
        motivo = request.POST.get('motivo', '').strip()

        if not insumo_id.isdecimal():
            messages.error(request, 'Selecciona un producto válido.')
        elif cantidad is None or cantidad <= 0 or not _decimal_fits_field(cantidad, 11, 3):
            messages.error(request, 'Introduce una cantidad positiva de hasta tres decimales.')
        elif len(motivo) > 200:
            messages.error(request, 'El motivo o referencia no puede superar 200 caracteres.')
        else:
            with transaction.atomic():
                insumo = Insumo.objects.select_for_update().filter(
                    pk=int(insumo_id),
                    activo=True,
                    tipo_inventario__in=['cocina', 'restaurante'],
                ).first()
                if insumo is None:
                    messages.error(request, 'El producto seleccionado no está activo o ya no existe.')
                else:
                    stock_anterior = insumo.stock_actual
                    stock_posterior = stock_anterior + cantidad
                    if not _decimal_fits_field(stock_posterior, 11, 3):
                        messages.error(request, 'La cantidad supera el límite de existencia permitido.')
                    else:
                        insumo.stock_actual = stock_posterior
                        insumo.save(update_fields=['stock_actual', 'actualizado_en'])
                        from .services import _registrar_movimiento
                        _registrar_movimiento(
                            insumo,
                            'entrada',
                            cantidad,
                            stock_anterior,
                            stock_posterior,
                            usuario=request.user,
                            motivo=motivo or 'Entrada manual por Caja',
                        )
                        messages.success(
                            request,
                            f'Entrada registrada para "{insumo.nombre}". Existencia actual: {stock_posterior} {insumo.unidad}.',
                        )
        return redirect('inventario:entrada_almacen_cajera')

    return render(request, 'inventario/entrada_almacen_cajera.html', {
        'inventarios': inventarios,
    })


@login_required
@user_passes_test(_es_admin)
@require_POST
def configurar_wifi(request):
    ssid = request.POST.get('ssid', '')
    password = request.POST.get('password', '')
    destino = reverse('inventario:wifi_qr')

    if not ssid.strip():
        messages.error(request, 'Debes indicar el nombre de la red Wi-Fi.')
    elif len(ssid.encode('utf-8')) > 32:
        messages.error(request, 'El nombre de la red Wi-Fi no puede superar 32 bytes.')
    elif password and not (8 <= len(password) <= 63):
        messages.error(request, 'La contraseña Wi-Fi debe tener entre 8 y 63 caracteres, o estar vacía para una red abierta.')
    else:
        configuracion, _ = ConfiguracionWifi.objects.get_or_create(pk=1)
        configuracion.ssid = ssid
        configuracion.password = password
        configuracion.save(update_fields=['ssid', 'password'])
        messages.success(request, 'La configuración Wi-Fi y su QR se actualizaron correctamente.')
    return redirect(destino)


@login_required
@user_passes_test(_es_admin)
def categoria_crear(request):
    if request.method == 'POST':
        nombre = request.POST.get('nombre', '').strip()
        if not nombre:
            messages.error(request, 'La categoría necesita un nombre.')
        elif Categoria.objects.filter(nombre__iexact=nombre).exists():
            messages.error(request, f'La categoría "{nombre}" ya existe.')
        else:
            Categoria.objects.create(nombre=nombre, orden=Categoria.objects.count() + 1)
            messages.success(request, f'Categoría "{nombre}" creada.')
    return redirect(_url_admin_seccion('configuracion', '#categorias'))


@login_required
@user_passes_test(_es_admin)
def categoria_editar(request, pk):
    categoria = get_object_or_404(Categoria, pk=pk)
    if request.method == 'POST':
        nombre = request.POST.get('nombre', '').strip()
        if not nombre:
            messages.error(request, 'La categoría necesita un nombre.')
        elif len(nombre) > Categoria._meta.get_field('nombre').max_length:
            messages.error(request, 'El nombre de la categoría no puede superar los 100 caracteres.')
        elif Categoria.objects.filter(nombre__iexact=nombre).exclude(pk=categoria.pk).exists():
            messages.error(request, f'La categoría "{nombre}" ya existe.')
        else:
            categoria.nombre = nombre
            categoria.save(update_fields=['nombre'])
            messages.success(request, f'Categoría actualizada a "{nombre}".')
    return redirect(_url_admin_seccion('configuracion', '#categorias'))


@login_required
@user_passes_test(_es_admin)
def categoria_eliminar(request, pk):
    categoria = get_object_or_404(Categoria, pk=pk)
    if request.method == 'POST':
        nombre = categoria.nombre
        categoria.delete()
        messages.success(
            request,
            f'Categoría "{nombre}" eliminada. Los platos se conservaron sin categoría.',
        )
    return redirect(_url_admin_seccion('configuracion', '#categorias'))


@login_required
@user_passes_test(_es_admin)
@require_POST
def categoria_alternar_visibilidad(request, pk):
    categoria = get_object_or_404(Categoria, pk=pk)
    categoria.visible = not categoria.visible
    categoria.save(update_fields=['visible'])
    estado = 'visible' if categoria.visible else 'oculta'
    messages.success(request, f'Categoría "{categoria.nombre}" ahora está {estado} en la carta.')
    return redirect(_url_admin_seccion('configuracion', '#categorias'))


@login_required
@user_passes_test(_es_admin)
@require_POST
def categoria_mover(request, pk):
    direccion = request.POST.get('direccion')
    if direccion not in {'up', 'down'}:
        messages.error(request, 'La dirección de reordenamiento no es válida.')
        return redirect(_url_admin_seccion('configuracion', '#categorias'))

    with transaction.atomic():
        categorias = list(
            Categoria.objects.select_for_update().order_by('orden', 'nombre', 'pk')
        )
        indice = next(
            (i for i, categoria in enumerate(categorias) if categoria.pk == pk),
            None,
        )
        if indice is None:
            raise Http404('La categoría no existe.')
        nuevo_indice = indice - 1 if direccion == 'up' else indice + 1
        if nuevo_indice < 0 or nuevo_indice >= len(categorias):
            messages.info(request, 'La categoría ya está en ese extremo del orden.')
        else:
            categorias[indice], categorias[nuevo_indice] = (
                categorias[nuevo_indice],
                categorias[indice],
            )
            for orden, categoria in enumerate(categorias, start=1):
                categoria.orden = orden
            Categoria.objects.bulk_update(categorias, ['orden'])
            messages.success(request, f'Orden de "{categorias[nuevo_indice].nombre}" actualizado.')
    return redirect(_url_admin_seccion('configuracion', '#categorias'))


@login_required
@user_passes_test(_es_admin)
def categoria_inventario_crear(request, tipo):
    nombre_tipo = dict(CategoriaInventario.TIPO_CHOICES).get(tipo)
    if not nombre_tipo:
        messages.error(request, 'Selecciona un inventario válido.')
        return redirect(_url_admin_seccion('configuracion'))
    if request.method == 'POST':
        nombre = request.POST.get('nombre', '').strip()
        if not nombre:
            messages.error(request, 'La categoría necesita un nombre.')
        elif len(nombre) > CategoriaInventario._meta.get_field('nombre').max_length:
            messages.error(request, 'El nombre de la categoría no puede superar los 100 caracteres.')
        elif CategoriaInventario.objects.filter(tipo=tipo, nombre__iexact=nombre).exists():
            messages.error(request, f'La categoría "{nombre}" ya existe en {nombre_tipo}.')
        else:
            CategoriaInventario.objects.create(
                tipo=tipo,
                nombre=nombre,
                orden=CategoriaInventario.objects.filter(tipo=tipo).count() + 1,
            )
            messages.success(request, f'Categoría "{nombre}" creada en {nombre_tipo}.')
    return redirect(_url_admin_seccion(tipo, f'#inventario-{tipo}'))


@login_required
@user_passes_test(_es_admin)
def categoria_inventario_editar(request, tipo, pk):
    categoria = get_object_or_404(CategoriaInventario, pk=pk, tipo=tipo)
    if request.method == 'POST':
        nombre = request.POST.get('nombre', '').strip()
        if not nombre:
            messages.error(request, 'La categoría necesita un nombre.')
        elif len(nombre) > CategoriaInventario._meta.get_field('nombre').max_length:
            messages.error(request, 'El nombre de la categoría no puede superar los 100 caracteres.')
        elif CategoriaInventario.objects.filter(
            tipo=tipo,
            nombre__iexact=nombre,
        ).exclude(pk=categoria.pk).exists():
            messages.error(request, f'La categoría "{nombre}" ya existe en este inventario.')
        else:
            categoria.nombre = nombre
            categoria.save(update_fields=['nombre'])
            messages.success(request, f'Categoría actualizada a "{nombre}".')
    return redirect(_url_admin_seccion(tipo, f'#categorias-{tipo}'))


@login_required
@user_passes_test(_es_admin)
def categoria_inventario_eliminar(request, tipo, pk):
    categoria = get_object_or_404(CategoriaInventario, pk=pk, tipo=tipo)
    if request.method == 'POST':
        nombre = categoria.nombre
        nombre_tipo = categoria.get_tipo_display()
        categoria.delete()
        messages.success(
            request,
            f'Categoría "{nombre}" eliminada de {nombre_tipo}. '
            'Los productos se conservaron sin categoría.',
        )
    return redirect(_url_admin_seccion(tipo, f'#categorias-{tipo}'))


@login_required
@user_passes_test(_es_admin_o_cajera)
def plato_crear(request):
    if request.method == 'POST':
        es_cajera = request.user.role == 'cajera'
        categoria_id = request.POST.get('categoria')
        categoria_nombre = '' if es_cajera else request.POST.get('categoria_nueva', '').strip()
        nombre = request.POST.get('nombre', '').strip()
        descripcion = request.POST.get('descripcion', '').strip()
        precio = _to_decimal(request.POST.get('precio'))
        destino = request.POST.get('destino', 'barra' if es_cajera else 'cocina')
        if es_cajera and destino != 'barra':
            messages.error(request, 'Solo puedes agregar platos al área de Restaurante.')
            return redirect(_url_gestion_carta(request.user))

        if not categoria_id and not categoria_nombre:
            messages.error(request, 'Debes elegir una categoría para el plato.')
            return redirect(_url_gestion_carta(request.user))

        if destino not in dict(Plato.DESTINO_CHOICES):
            messages.error(request, 'Selecciona Cocina o Barra como área de preparación.')
            return redirect(_url_gestion_carta(request.user))

        if not nombre or precio is None or precio <= 0:
            messages.error(request, 'Completa nombre y precio del plato.')
            return redirect(_url_gestion_carta(request.user))

        receta, error_receta = _receta_desde_post(request)
        guarniciones, error_guarniciones = _guarniciones_desde_post(request)
        disponible = request.POST.get('disponible') == 'on'
        if error_receta:
            messages.error(request, error_receta)
            return redirect(_url_gestion_carta(request.user))
        if error_guarniciones:
            messages.error(request, error_guarniciones)
            return redirect(_url_gestion_carta(request.user))
        if disponible and not receta and not es_cajera:
            messages.error(request, 'Agrega al menos un producto de inventario antes de publicar el plato.')
            return redirect(_url_gestion_carta(request.user))
        if disponible and receta_tiene_insumo_sin_stock(receta):
            messages.error(request, 'No se puede publicar el plato porque uno o más productos de su receta no tienen stock.')
            return redirect(_url_gestion_carta(request.user))

        if categoria_id:
            categoria = get_object_or_404(Categoria, pk=categoria_id)
        else:
            categoria, created = Categoria.objects.get_or_create(
                nombre__iexact=categoria_nombre,
                defaults={'nombre': categoria_nombre, 'orden': Categoria.objects.count() + 1}
            )
            if created:
                messages.success(request, f'Categoría "{categoria.nombre}" creada.')

        with transaction.atomic():
            plato = Plato.objects.create(
                categoria=categoria,
                nombre=nombre,
                descripcion=descripcion,
                precio=precio,
                destino=destino,
                disponible=disponible,
                trae_guarniciones_incluidas=bool(guarniciones),
                orden=Plato.objects.count() + 1,
                imagen=request.FILES.get('imagen'),
            )
            plato.guarniciones_incluidas.set(guarniciones)
            RecetaItem.objects.bulk_create([
                RecetaItem(plato=plato, insumo=insumo, cantidad=cantidad)
                for insumo, cantidad in receta
            ])
        messages.success(request, f'Plato "{nombre}" creado con {len(receta)} producto(s) de inventario.')
    return redirect(_url_gestion_carta(request.user))


@login_required
@user_passes_test(_es_admin_o_cajera)
def plato_editar(request, pk):
    plato = get_object_or_404(Plato, pk=pk)
    es_cajera = request.user.role == 'cajera'
    if es_cajera and plato.destino != 'barra':
        if request.method == 'POST':
            precio = _to_decimal(request.POST.get('precio'))
            if precio is None or precio <= 0:
                messages.error(request, 'Ingresa un precio válido.')
            else:
                plato.precio = precio
                plato.save(update_fields=['precio'])
                messages.success(request, f'Precio de "{plato.nombre}" actualizado.')
        return redirect(_url_gestion_carta(request.user))

    if request.method == 'POST':
        categoria_id = request.POST.get('categoria')
        nombre = request.POST.get('nombre', plato.nombre).strip() or plato.nombre
        descripcion = request.POST.get('descripcion', plato.descripcion).strip()
        precio = _to_decimal(request.POST.get('precio'))
        if es_cajera and (precio is None or precio <= 0):
            messages.error(request, 'Ingresa un precio válido.')
            return redirect(_url_gestion_carta(request.user))
        destino = request.POST.get('destino', plato.destino)
        if es_cajera and destino != 'barra':
            messages.error(request, 'No tienes permiso para cambiar el área de preparación.')
            return redirect(_url_gestion_carta(request.user))
        if destino not in dict(Plato.DESTINO_CHOICES):
            messages.error(request, 'Selecciona Cocina o Barra como área de preparación.')
            return redirect(_url_gestion_carta(request.user))

        receta = None
        if 'receta_enviada' in request.POST:
            receta, error_receta = _receta_desde_post(request)
            if error_receta:
                messages.error(request, error_receta)
                return redirect(_url_gestion_carta(request.user))
        guarniciones, error_guarniciones = _guarniciones_desde_post(request)
        if error_guarniciones:
            messages.error(request, error_guarniciones)
            return redirect(_url_gestion_carta(request.user))
        disponible = request.POST.get('disponible') == 'on'
        if disponible and receta is not None and not receta and not es_cajera:
            messages.error(request, 'Agrega al menos un producto de inventario para publicar este plato.')
            return redirect(_url_gestion_carta(request.user))
        if disponible and receta is None and not plato.receta.exists() and not es_cajera:
            messages.error(request, 'Agrega al menos un producto de inventario para publicar este plato.')
            return redirect(_url_gestion_carta(request.user))
        receta_para_validar = receta if receta is not None else [
            (item.insumo, item.cantidad)
            for item in plato.receta.select_related('insumo')
        ]
        if disponible and receta_tiene_insumo_sin_stock(receta_para_validar):
            messages.error(request, 'No se puede publicar el plato porque uno o más productos de su receta no tienen stock.')
            return redirect(_url_gestion_carta(request.user))
        if 'categoria' in request.POST:
            plato.categoria = get_object_or_404(Categoria, pk=categoria_id) if categoria_id else None
        if precio is not None and precio > 0:
            plato.precio = precio
        plato.nombre = nombre
        plato.descripcion = descripcion
        plato.destino = destino
        plato.disponible = disponible
        plato.trae_guarniciones_incluidas = bool(guarniciones)
        if request.FILES.get('imagen'):
            plato.imagen = request.FILES['imagen']
        with transaction.atomic():
            plato.save()
            plato.guarniciones_incluidas.set(guarniciones)
            if receta is not None:
                plato.receta.all().delete()
                RecetaItem.objects.bulk_create([
                    RecetaItem(plato=plato, insumo=insumo, cantidad=cantidad)
                    for insumo, cantidad in receta
                ])
        messages.success(request, f'Plato "{plato.nombre}" actualizado.')
    return redirect(_url_gestion_carta(request.user))


@login_required
@user_passes_test(_es_admin)
@require_POST
def plato_alternar_disponibilidad(request, pk):
    plato = get_object_or_404(Plato, pk=pk)
    if not plato.disponible:
        receta = [
            (item.insumo, item.cantidad)
            for item in plato.receta.select_related('insumo')
        ]
        if not receta:
            messages.error(request, 'Agrega al menos un producto de inventario antes de publicar este plato.')
            return redirect(_url_admin_seccion('carta', '#carta-platos'))
        if receta_tiene_insumo_sin_stock(receta):
            messages.error(request, 'No se puede publicar el plato porque uno o más productos de su receta no tienen stock.')
            return redirect(_url_admin_seccion('carta', '#carta-platos'))
        plato.disponible = True
    else:
        plato.disponible = False
    plato.save(update_fields=['disponible'])
    messages.success(
        request,
        f'Plato "{plato.nombre}" {"marcado como disponible" if plato.disponible else "marcado como agotado"}.',
    )
    return redirect(_url_admin_seccion('carta', '#carta-platos'))


@login_required
@user_passes_test(_es_admin)
@require_POST
def guarnicion_crear(request):
    nombre = request.POST.get('nombre', '').strip()
    descripcion = request.POST.get('descripcion', '').strip()
    receta, error_receta = _receta_desde_post(
        request, 'guarnicion_insumo_id[]', 'guarnicion_cantidad[]',
    )
    if not nombre or len(nombre) > 120:
        messages.error(request, 'Indica un nombre válido para la guarnición (hasta 120 caracteres).')
        return redirect(_url_admin_seccion('carta', '#carta-guarniciones'))
    if Guarnicion.objects.filter(nombre__iexact=nombre).exists():
        messages.error(request, 'Ya existe una guarnición con ese nombre.')
        return redirect(_url_admin_seccion('carta', '#carta-guarniciones'))
    if error_receta or not receta:
        messages.error(request, error_receta or 'Agrega al menos un producto de inventario a la receta.')
        return redirect(_url_admin_seccion('carta', '#carta-guarniciones'))
    if receta_tiene_insumo_sin_stock(receta):
        messages.error(request, 'No se puede activar una guarnición con productos sin stock.')
        return redirect(_url_admin_seccion('carta', '#carta-guarniciones'))
    with transaction.atomic():
        guarnicion = Guarnicion.objects.create(nombre=nombre, descripcion=descripcion)
        GuarnicionRecetaItem.objects.bulk_create([
            GuarnicionRecetaItem(guarnicion=guarnicion, insumo=insumo, cantidad=cantidad)
            for insumo, cantidad in receta
        ])
    messages.success(request, f'Guarnición "{nombre}" creada con {len(receta)} producto(s) de inventario.')
    return redirect(_url_admin_seccion('carta', '#carta-guarniciones'))


@login_required
@user_passes_test(_es_admin)
@require_POST
def guarnicion_editar(request, pk):
    guarnicion = get_object_or_404(Guarnicion, pk=pk)
    nombre = request.POST.get('nombre', '').strip()
    descripcion = request.POST.get('descripcion', '').strip()
    receta, error_receta = _receta_desde_post(
        request, 'guarnicion_insumo_id[]', 'guarnicion_cantidad[]',
    )
    if not nombre or len(nombre) > 120:
        messages.error(request, 'Indica un nombre válido para la guarnición (hasta 120 caracteres).')
        return redirect(_url_admin_seccion('carta', '#carta-guarniciones'))
    if Guarnicion.objects.filter(nombre__iexact=nombre).exclude(pk=pk).exists():
        messages.error(request, 'Ya existe otra guarnición con ese nombre.')
        return redirect(_url_admin_seccion('carta', '#carta-guarniciones'))
    if error_receta or not receta:
        messages.error(request, error_receta or 'La guarnición necesita al menos un producto de inventario.')
        return redirect(_url_admin_seccion('carta', '#carta-guarniciones'))
    activa = request.POST.get('activa') == 'on'
    if activa and receta_tiene_insumo_sin_stock(receta):
        messages.error(request, 'No se puede activar una guarnición con productos sin stock.')
        return redirect(_url_admin_seccion('carta', '#carta-guarniciones'))
    with transaction.atomic():
        guarnicion.nombre = nombre
        guarnicion.descripcion = descripcion
        guarnicion.activa = activa
        guarnicion.save(update_fields=['nombre', 'descripcion', 'activa'])
        guarnicion.receta.all().delete()
        GuarnicionRecetaItem.objects.bulk_create([
            GuarnicionRecetaItem(guarnicion=guarnicion, insumo=insumo, cantidad=cantidad)
            for insumo, cantidad in receta
        ])
    messages.success(request, f'Guarnición "{nombre}" actualizada.')
    return redirect(_url_admin_seccion('carta', '#carta-guarniciones'))


@login_required
@user_passes_test(_es_admin)
@require_POST
def guarnicion_eliminar(request, pk):
    guarnicion = get_object_or_404(Guarnicion, pk=pk)
    nombre = guarnicion.nombre
    guarnicion.delete()
    messages.success(request, f'Guarnición "{nombre}" eliminada.')
    return redirect(_url_admin_seccion('carta', '#carta-guarniciones'))


@login_required
@user_passes_test(_es_admin)
@require_POST
def guarnicion_alternar_estado(request, pk):
    guarnicion = get_object_or_404(Guarnicion, pk=pk)
    guarnicion.activa = not guarnicion.activa
    guarnicion.save(update_fields=['activa'])
    messages.success(
        request,
        f'Guarnición "{guarnicion.nombre}" {"activada" if guarnicion.activa else "desactivada"}.',
    )
    return redirect(_url_admin_seccion('carta', '#carta-guarniciones'))


@login_required
@user_passes_test(_es_admin_o_cajera)
def plato_eliminar(request, pk):
    plato = get_object_or_404(Plato, pk=pk)
    if request.user.role == 'cajera':
        messages.error(request, 'No tienes permiso para eliminar platos de la carta.')
        return redirect(_url_gestion_carta(request.user))
    if request.method == 'POST':
        nombre = plato.nombre
        plato.delete()
        messages.success(request, f'Plato "{nombre}" eliminado.')
    return redirect(_url_admin_seccion('carta'))


@login_required
@user_passes_test(_es_admin)
def insumo_crear(request):
    if request.method == 'POST':
        nombre = request.POST.get('nombre', '').strip()
        unidad = request.POST.get('unidad', 'unidad')
        tipo_inventario = request.POST.get('tipo_inventario')
        stock_actual = _to_decimal(request.POST.get('stock_actual'), Decimal('0'))
        stock_minimo = _to_decimal(request.POST.get('stock_minimo'), Decimal('0'))
        stock_maximo = _to_decimal(request.POST.get('stock_maximo'), Decimal('0'))
        costo = _to_decimal(request.POST.get('costo'), Decimal('0'))
        precio = _to_decimal(request.POST.get('precio'), Decimal('0'))
        categoria_inventario_id = request.POST.get('categoria_inventario')
        if tipo_inventario not in dict(CategoriaInventario.TIPO_CHOICES):
            messages.error(request, 'Selecciona Cocina o Restaurante para el producto.')
        elif len(nombre) > 150:
            messages.error(request, 'El nombre del producto no puede superar 150 caracteres.')
        elif unidad not in dict(Insumo.UNIDAD_CHOICES):
            messages.error(request, 'Selecciona una unidad válida.')
        elif not all((
            _decimal_fits_field(stock_actual, 11, 3),
            _decimal_fits_field(stock_minimo, 11, 3),
            _decimal_fits_field(stock_maximo, 12, 3),
            _decimal_fits_field(costo, 8, 2),
            _decimal_fits_field(precio, 8, 2),
        )):
            messages.error(request, 'Revisa los valores: existencias admiten hasta 8 enteros y 3 decimales; costo y precio, hasta 6 enteros y 2 decimales. No se permiten negativos.')
        elif not nombre:
            messages.error(request, 'El insumo necesita un nombre.')
        elif Insumo.objects.filter(nombre__iexact=nombre).exists():
            messages.error(request, f'Ya existe un insumo llamado "{nombre}".')
        else:
            activo = request.POST.get('activo', 'on') == 'on'
            categoria_inventario = None
            if categoria_inventario_id:
                categoria_inventario = CategoriaInventario.objects.filter(
                    pk=categoria_inventario_id,
                    tipo=tipo_inventario,
                ).first()
                if not categoria_inventario:
                    messages.error(request, 'La categoría no pertenece al inventario seleccionado.')
                    return redirect(_url_inventario_admin(tipo_inventario))
            insumo = Insumo.objects.create(
                nombre=nombre,
                tipo_inventario=tipo_inventario,
                categoria_inventario=categoria_inventario,
                unidad=unidad,
                stock_actual=stock_actual or Decimal('0'),
                stock_minimo=stock_minimo or Decimal('0'),
                stock_maximo=stock_maximo or Decimal('0'),
                costo=costo or Decimal('0'),
                precio=precio or Decimal('0'),
                disponible=activo,
                activo=activo,
            )
            if insumo.stock_actual > 0:
                from .services import _registrar_movimiento
                _registrar_movimiento(
                    insumo, 'entrada', insumo.stock_actual, Decimal('0'), insumo.stock_actual,
                    usuario=request.user, motivo='Inventario inicial',
                )
            messages.success(request, f'Producto "{nombre}" creado en {dict(CategoriaInventario.TIPO_CHOICES)[tipo_inventario]}.')
    return redirect(_url_inventario_admin(request.POST.get('tipo_inventario', 'restaurante')))


@login_required
@user_passes_test(_es_admin)
def insumo_editar(request, pk):
    insumo = get_object_or_404(Insumo, pk=pk)
    if request.method == 'POST':
        nombre = request.POST.get('nombre', insumo.nombre).strip() or insumo.nombre
        unidad = request.POST.get('unidad', insumo.unidad)
        tipo_inventario = request.POST.get('tipo_inventario', insumo.tipo_inventario)
        if tipo_inventario not in dict(CategoriaInventario.TIPO_CHOICES):
            messages.error(request, 'Selecciona Cocina o Restaurante para el producto.')
            return redirect(_url_inventario_admin(insumo.tipo_inventario))
        stock_actual = _to_decimal(request.POST.get('stock_actual'))
        stock_minimo = _to_decimal(request.POST.get('stock_minimo'))
        stock_maximo = _to_decimal(request.POST.get('stock_maximo'))
        costo = _to_decimal(request.POST.get('costo'))
        precio = _to_decimal(request.POST.get('precio'))
        valores_decimales_validos = all((
            (not request.POST.get(campo, '').strip()) or _decimal_fits_field(
                valor,
                max_digits,
                decimal_places,
                allow_negative=(campo == 'stock_actual'),
            )
            for campo, valor, max_digits, decimal_places in (
                ('stock_actual', stock_actual, 11, 3),
                ('stock_minimo', stock_minimo, 11, 3),
                ('stock_maximo', stock_maximo, 12, 3),
                ('costo', costo, 8, 2),
                ('precio', precio, 8, 2),
            )
        ))
        if len(nombre) > 150:
            messages.error(request, 'El nombre del producto no puede superar 150 caracteres.')
            return redirect(_url_inventario_admin(tipo_inventario))
        if unidad not in dict(Insumo.UNIDAD_CHOICES):
            messages.error(request, 'Selecciona una unidad válida.')
            return redirect(_url_inventario_admin(tipo_inventario))
        if not valores_decimales_validos:
            messages.error(request, 'Revisa los valores: existencias admiten hasta 8 enteros y 3 decimales; costo y precio, hasta 6 enteros y 2 decimales. No se permiten negativos excepto en existencia actual.')
            return redirect(_url_inventario_admin(tipo_inventario))
        if Insumo.objects.filter(nombre__iexact=nombre).exclude(pk=insumo.pk).exists():
            messages.error(request, f'Ya existe un producto llamado "{nombre}".')
            return redirect(_url_inventario_admin(tipo_inventario))
        insumo.nombre = nombre
        insumo.unidad = unidad
        insumo.tipo_inventario = tipo_inventario
        categoria_inventario_id = request.POST.get('categoria_inventario')
        categoria_inventario = None
        if categoria_inventario_id:
            categoria_inventario = CategoriaInventario.objects.filter(
                pk=categoria_inventario_id,
                tipo=tipo_inventario,
            ).first()
            if not categoria_inventario:
                messages.error(request, 'La categoría no pertenece al inventario seleccionado.')
                return redirect(_url_inventario_admin(tipo_inventario))
        insumo.categoria_inventario = categoria_inventario
        if stock_actual is not None:
            stock_anterior = insumo.stock_actual
            insumo.stock_actual = stock_actual
        else:
            stock_anterior = insumo.stock_actual
        if stock_minimo is not None:
            insumo.stock_minimo = stock_minimo
        if stock_maximo is not None:
            insumo.stock_maximo = stock_maximo
        if costo is not None:
            insumo.costo = costo
        if precio is not None:
            insumo.precio = precio
        if 'activo' in request.POST:
            insumo.activo = request.POST.get('activo') == 'on'
            insumo.disponible = insumo.activo
        insumo.save()
        if stock_actual is not None and stock_actual != stock_anterior:
            from .services import _registrar_movimiento
            tipo = 'entrada' if stock_actual > stock_anterior else 'salida'
            _registrar_movimiento(
                insumo,
                tipo,
                abs(stock_actual - stock_anterior),
                stock_anterior,
                stock_actual,
                usuario=request.user,
                motivo='Ajuste manual del inventario',
            )
        messages.success(request, f'Insumo "{insumo.nombre}" actualizado.')
        return redirect(_url_inventario_admin(tipo_inventario))
    return redirect(_url_admin_seccion('configuracion'))


@login_required
@user_passes_test(_es_admin)
def movimiento_crear(request):
    insumo = None
    if request.method == 'POST':
        insumo = get_object_or_404(Insumo, pk=request.POST.get('insumo_id'))
        tipo = request.POST.get('tipo')
        cantidad = _to_decimal(request.POST.get('cantidad'))
        motivo = request.POST.get('motivo', '').strip()
        if tipo not in dict(MovimientoInventario.TIPO_CHOICES) or cantidad is None or cantidad <= 0:
            messages.error(request, 'Selecciona un tipo y una cantidad válida.')
        else:
            stock_anterior = insumo.stock_actual
            stock_posterior = stock_anterior + cantidad if tipo == 'entrada' else stock_anterior - cantidad
            insumo.stock_actual = stock_posterior
            insumo.save(update_fields=['stock_actual', 'actualizado_en'])
            from .services import _registrar_movimiento
            _registrar_movimiento(
                insumo, tipo, cantidad, stock_anterior, stock_posterior,
                usuario=request.user, motivo=motivo or 'Movimiento manual',
            )
            messages.success(request, f'Movimiento registrado para "{insumo.nombre}".')
    if insumo and insumo.tipo_inventario:
        return redirect(_url_inventario_admin(insumo.tipo_inventario))
    return redirect(_url_admin_seccion('configuracion', '#movimientos-inventario'))


@login_required
@user_passes_test(_es_admin)
def insumo_eliminar(request, pk):
    insumo = get_object_or_404(Insumo, pk=pk)
    if request.method == 'POST':
        nombre = insumo.nombre
        insumo.delete()
        messages.success(request, f'Insumo "{nombre}" eliminado.')
    return redirect(_url_inventario_admin(insumo.tipo_inventario))


@login_required
@user_passes_test(_es_admin)
def receta_agregar(request, plato_id):
    plato = get_object_or_404(Plato, pk=plato_id)
    if request.method == 'POST':
        insumo_id = request.POST.get('insumo_id')
        cantidad = _to_decimal(request.POST.get('cantidad'), Decimal('1'))
        insumo = Insumo.objects.filter(
            pk=insumo_id,
            activo=True,
            tipo_inventario__in=['cocina', 'restaurante'],
        ).first()
        if not insumo:
            messages.error(request, 'Selecciona un insumo válido.')
        elif not cantidad or cantidad <= 0:
            messages.error(request, 'La cantidad debe ser mayor que cero.')
        else:
            RecetaItem.objects.update_or_create(
                plato=plato, insumo=insumo, defaults={'cantidad': cantidad}
            )
            messages.success(request, f'"{insumo.nombre}" agregado a la receta de {plato.nombre}.')
    return redirect(_url_admin_seccion('carta', '#carta-platos'))


@login_required
@user_passes_test(_es_admin)
def receta_item_eliminar(request, pk):
    item = get_object_or_404(RecetaItem, pk=pk)
    plato = item.plato
    if request.method == 'POST':
        item.delete()
        if not plato.receta.exists():
            plato.disponible = False
            plato.save(update_fields=['disponible'])
        messages.success(request, 'Ingrediente quitado de la receta.')
    return redirect(_url_admin_seccion('carta', '#carta-platos'))


@login_required
@user_passes_test(_es_admin)
def tasa_actualizar(request):
    if request.method == 'POST':
        valor = _to_decimal(request.POST.get('valor'))
        if not valor or valor <= 0:
            messages.error(request, 'Ingresa una tasa de cambio válida.')
        else:
            TasaCambio.objects.create(valor=valor)
            messages.success(request, f'Tasa de cambio actualizada: 1 USD = {valor} CUP.')
    return redirect(_url_admin_seccion('configuracion'))
