import json
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.db.models import Prefetch
from django.core.paginator import Paginator
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_POST

from carta.models import Categoria, Plato
from carta.qr import generar_qr_wifi
from .models import CategoriaInventario, Insumo, MovimientoInventario, RecetaItem, TasaCambio
from usuarios.models import ConfiguracionWifi


def _es_admin(user):
    return user.is_authenticated and user.role == 'administrador'


def _to_decimal(valor, default=None):
    try:
        return Decimal(str(valor).replace(',', '.').strip())
    except (InvalidOperation, ValueError, AttributeError):
        return default


def _pagination_query(request, page_parameter):
    query = request.GET.copy()
    query.pop(page_parameter, None)
    return query.urlencode()


def _receta_desde_post(request):
    insumo_ids = request.POST.getlist('insumo_id[]') or request.POST.getlist('insumo_id')
    cantidades = request.POST.getlist('cantidad[]') or request.POST.getlist('cantidad')
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


@login_required
@user_passes_test(_es_admin)
def administrador(request):
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
    platos_sin_categoria = [
        plato for plato in platos
        if plato.categoria_id is None and plato.disponible and plato.receta_admin
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
    insumos_por_clasificar = Paginator(
        insumos.filter(tipo_inventario__isnull=True).order_by('nombre'),
        25,
    ).get_page(request.GET.get('sin_clasificar_page'))
    wifi_configuracion = ConfiguracionWifi.objects.filter(pk=1).first()
    wifi_qr = (
        generar_qr_wifi(wifi_configuracion.ssid, wifi_configuracion.password)
        if wifi_configuracion and wifi_configuracion.ssid
        else None
    )

    return render(request, 'administrador/administrador.html', {
        'insumos': insumos,
        'inventarios': inventarios,
        'insumos_por_clasificar': insumos_por_clasificar,
        'sin_clasificar_pagination_query': _pagination_query(request, 'sin_clasificar_page'),
        'sin_clasificar_pagination_anchor': '#inventario-por-clasificar',
        'categorias_inventario': CategoriaInventario.objects.all(),
        'movimientos': movimientos,
        'movimientos_pagination_query': movimientos_pagina_query,
        'movimientos_pagination_anchor': '#movimientos-inventario',
        'platos': platos,
        'categorias': categorias,
        'platos_sin_categoria': platos_sin_categoria,
        'plato_sel': plato_sel,
        'receta_items': receta_items,
        'insumos_disponibles': Insumo.objects.filter(
            activo=True,
            tipo_inventario__in=['cocina', 'restaurante'],
        ).select_related('categoria_inventario').order_by('tipo_inventario', 'nombre'),
        'tasa_actual': TasaCambio.actual(),
        'wifi_configuracion': wifi_configuracion,
        'wifi_qr': wifi_qr,
    })


@login_required
@user_passes_test(_es_admin)
@require_POST
def configurar_wifi(request):
    ssid = request.POST.get('ssid', '')
    password = request.POST.get('password', '')
    destino = f'{reverse("inventario:administrador")}#wifi'

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
    return redirect('/administrador/#categorias')


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
    return redirect('/administrador/#categorias')


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
    return redirect('/administrador/#categorias')


@login_required
@user_passes_test(_es_admin)
def categoria_inventario_crear(request, tipo):
    nombre_tipo = dict(CategoriaInventario.TIPO_CHOICES).get(tipo)
    if not nombre_tipo:
        messages.error(request, 'Selecciona un inventario válido.')
        return redirect('inventario:administrador')
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
    return redirect(f'/administrador/#inventario-{tipo}')


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
    return redirect(f'/administrador/#inventario-{tipo}')


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
    return redirect(f'/administrador/#inventario-{tipo}')


@login_required
@user_passes_test(_es_admin)
def plato_crear(request):
    if request.method == 'POST':
        categoria_id = request.POST.get('categoria')
        categoria_nombre = request.POST.get('categoria_nueva', '').strip()
        nombre = request.POST.get('nombre', '').strip()
        descripcion = request.POST.get('descripcion', '').strip()
        precio = _to_decimal(request.POST.get('precio'))

        if not categoria_id and not categoria_nombre:
            messages.error(request, 'Debes elegir una categoría para el plato.')
            return redirect('/administrador/#carta')

        if not nombre or precio is None or precio <= 0:
            messages.error(request, 'Completa nombre y precio del plato.')
            return redirect('/administrador/#carta')

        receta, error_receta = _receta_desde_post(request)
        disponible = request.POST.get('disponible') == 'on'
        if error_receta:
            messages.error(request, error_receta)
            return redirect('/administrador/#carta')
        if disponible and not receta:
            messages.error(request, 'Agrega al menos un producto de inventario antes de publicar el plato.')
            return redirect('/administrador/#carta')

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
                disponible=disponible,
                orden=Plato.objects.count() + 1,
                imagen=request.FILES.get('imagen'),
            )
            RecetaItem.objects.bulk_create([
                RecetaItem(plato=plato, insumo=insumo, cantidad=cantidad)
                for insumo, cantidad in receta
            ])
        messages.success(request, f'Plato "{nombre}" creado con {len(receta)} producto(s) de inventario.')
    return redirect('/administrador/#carta')


@login_required
@user_passes_test(_es_admin)
def plato_editar(request, pk):
    plato = get_object_or_404(Plato, pk=pk)
    if request.method == 'POST':
        categoria_id = request.POST.get('categoria')
        nombre = request.POST.get('nombre', plato.nombre).strip() or plato.nombre
        descripcion = request.POST.get('descripcion', plato.descripcion).strip()
        precio = _to_decimal(request.POST.get('precio'))
        receta = None
        if 'receta_enviada' in request.POST:
            receta, error_receta = _receta_desde_post(request)
            if error_receta:
                messages.error(request, error_receta)
                return redirect('/administrador/#carta')
        disponible = request.POST.get('disponible') == 'on'
        if disponible and receta is not None and not receta:
            messages.error(request, 'Agrega al menos un producto de inventario para publicar este plato.')
            return redirect('/administrador/#carta')
        if disponible and receta is None and not plato.receta.exists():
            messages.error(request, 'Agrega al menos un producto de inventario para publicar este plato.')
            return redirect('/administrador/#carta')
        if 'categoria' in request.POST:
            plato.categoria = get_object_or_404(Categoria, pk=categoria_id) if categoria_id else None
        if precio is not None and precio > 0:
            plato.precio = precio
        plato.nombre = nombre
        plato.descripcion = descripcion
        plato.disponible = disponible
        if request.FILES.get('imagen'):
            plato.imagen = request.FILES['imagen']
        with transaction.atomic():
            plato.save()
            if receta is not None:
                plato.receta.all().delete()
                RecetaItem.objects.bulk_create([
                    RecetaItem(plato=plato, insumo=insumo, cantidad=cantidad)
                    for insumo, cantidad in receta
                ])
        messages.success(request, f'Plato "{plato.nombre}" actualizado.')
    return redirect('/administrador/#carta')


@login_required
@user_passes_test(_es_admin)
def plato_eliminar(request, pk):
    plato = get_object_or_404(Plato, pk=pk)
    if request.method == 'POST':
        nombre = plato.nombre
        plato.delete()
        messages.success(request, f'Plato "{nombre}" eliminado.')
    return redirect('inventario:administrador')


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
        elif not nombre:
            messages.error(request, 'El insumo necesita un nombre.')
        elif Insumo.objects.filter(nombre__iexact=nombre).exists():
            messages.error(request, f'Ya existe un insumo llamado "{nombre}".')
        else:
            categoria_inventario = None
            if categoria_inventario_id:
                categoria_inventario = CategoriaInventario.objects.filter(
                    pk=categoria_inventario_id,
                    tipo=tipo_inventario,
                ).first()
                if not categoria_inventario:
                    messages.error(request, 'La categoría no pertenece al inventario seleccionado.')
                    return redirect(f'/administrador/#inventario-{tipo_inventario}')
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
                disponible=True,
                activo=True,
            )
            if insumo.stock_actual > 0:
                from .services import _registrar_movimiento
                _registrar_movimiento(
                    insumo, 'entrada', insumo.stock_actual, Decimal('0'), insumo.stock_actual,
                    usuario=request.user, motivo='Inventario inicial',
                )
            messages.success(request, f'Producto "{nombre}" creado en {dict(CategoriaInventario.TIPO_CHOICES)[tipo_inventario]}.')
    return redirect(f"/administrador/#inventario-{request.POST.get('tipo_inventario', 'restaurante')}")


@login_required
@user_passes_test(_es_admin)
def insumo_editar(request, pk):
    insumo = get_object_or_404(Insumo, pk=pk)
    if request.method == 'POST':
        insumo.nombre = request.POST.get('nombre', insumo.nombre).strip() or insumo.nombre
        insumo.unidad = request.POST.get('unidad', insumo.unidad)
        tipo_inventario = request.POST.get('tipo_inventario', insumo.tipo_inventario)
        if tipo_inventario not in dict(CategoriaInventario.TIPO_CHOICES):
            messages.error(request, 'Selecciona Cocina o Restaurante para el producto.')
            return redirect('inventario:administrador')
        insumo.tipo_inventario = tipo_inventario
        stock_actual = _to_decimal(request.POST.get('stock_actual'))
        stock_minimo = _to_decimal(request.POST.get('stock_minimo'))
        stock_maximo = _to_decimal(request.POST.get('stock_maximo'))
        costo = _to_decimal(request.POST.get('costo'))
        precio = _to_decimal(request.POST.get('precio'))
        categoria_inventario_id = request.POST.get('categoria_inventario')
        categoria_inventario = None
        if categoria_inventario_id:
            categoria_inventario = CategoriaInventario.objects.filter(
                pk=categoria_inventario_id,
                tipo=tipo_inventario,
            ).first()
            if not categoria_inventario:
                messages.error(request, 'La categoría no pertenece al inventario seleccionado.')
                return redirect(f'/administrador/#inventario-{tipo_inventario}')
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
        return redirect(f'/administrador/#inventario-{tipo_inventario}')
    return redirect('inventario:administrador')


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
        return redirect(f'/administrador/#inventario-{insumo.tipo_inventario}')
    return redirect('/administrador/#movimientos-inventario')


@login_required
@user_passes_test(_es_admin)
def insumo_eliminar(request, pk):
    insumo = get_object_or_404(Insumo, pk=pk)
    if request.method == 'POST':
        nombre = insumo.nombre
        insumo.delete()
        messages.success(request, f'Insumo "{nombre}" eliminado.')
    return redirect('inventario:administrador')


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
    return redirect('/administrador/#carta')


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
    return redirect('/administrador/#carta')


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
    return redirect('inventario:administrador')
