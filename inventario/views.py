from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import render, redirect, get_object_or_404

from carta.models import Categoria, Plato
from .models import Insumo, MovimientoInventario, RecetaItem, TasaCambio


def _es_admin(user):
    return user.is_authenticated and user.role == 'administrador'


def _to_decimal(valor, default=None):
    try:
        return Decimal(str(valor).replace(',', '.').strip())
    except (InvalidOperation, ValueError, AttributeError):
        return default


@login_required
@user_passes_test(_es_admin)
def administrador(request):
    insumos = Insumo.objects.select_related('categoria').all()
    movimientos = MovimientoInventario.objects.select_related('insumo', 'usuario').all()[:250]
    platos = list(Plato.objects.select_related('categoria').all())
    precios_inventario = {
        insumo.nombre.casefold(): insumo.precio
        for insumo in insumos
    }
    for plato in platos:
        plato.precio_inventario = precios_inventario.get(plato.nombre.casefold(), plato.precio)
    categorias = Categoria.objects.all()

    plato_sel = None
    plato_id = request.GET.get('plato')
    if plato_id:
        plato_sel = Plato.objects.filter(pk=plato_id).first()
    receta_items = []
    if plato_sel:
        receta_items = RecetaItem.objects.filter(plato=plato_sel).select_related('insumo')

    return render(request, 'administrador/administrador.html', {
        'insumos': insumos,
        'movimientos': movimientos,
        'platos': platos,
        'categorias': categorias,
        'plato_sel': plato_sel,
        'receta_items': receta_items,
        'insumos_disponibles': Insumo.objects.filter(activo=True),
        'tasa_actual': TasaCambio.actual(),
    })


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
    return redirect('/administrador/#inventario')


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
            messages.error(request, 'Debes elegir una categoría o crear una nueva.')
            return redirect('inventario:administrador')

        if not nombre or precio is None or precio <= 0:
            messages.error(request, 'Completa nombre y precio del plato.')
            return redirect('inventario:administrador')

        if categoria_id:
            categoria = get_object_or_404(Categoria, pk=categoria_id)
        else:
            categoria, created = Categoria.objects.get_or_create(
                nombre__iexact=categoria_nombre,
                defaults={'nombre': categoria_nombre, 'orden': Categoria.objects.count() + 1}
            )
            if created:
                messages.success(request, f'Categoría "{categoria.nombre}" creada.')

        plato = Plato.objects.create(
            categoria=categoria,
            nombre=nombre,
            descripcion=descripcion,
            precio=precio,
            disponible=request.POST.get('disponible') == 'on',
            orden=Plato.objects.count() + 1,
            imagen=request.FILES.get('imagen'),
        )

        insumo_ids = request.POST.getlist('insumo_id[]') or request.POST.getlist('insumo_id')
        cantidades = request.POST.getlist('cantidad[]') or request.POST.getlist('cantidad')
        creados = 0
        for insumo_id, cantidad_raw in zip(insumo_ids, cantidades):
            insumo_id = (insumo_id or '').strip()
            cantidad = _to_decimal(cantidad_raw)
            if not insumo_id or cantidad is None or cantidad <= 0:
                continue
            insumo = Insumo.objects.filter(pk=insumo_id).first()
            if not insumo:
                continue
            RecetaItem.objects.update_or_create(
                plato=plato, insumo=insumo,
                defaults={'cantidad': cantidad}
            )
            creados += 1

        if creados:
            messages.success(request, f'Plato "{nombre}" creado con {creados} ingrediente(s) de inventario.')
        else:
            messages.success(request, f'Plato "{nombre}" creado sin receta asociada.')
    return redirect('inventario:administrador')


@login_required
@user_passes_test(_es_admin)
def plato_editar(request, pk):
    plato = get_object_or_404(Plato, pk=pk)
    if request.method == 'POST':
        insumo_relacionado = Insumo.objects.filter(nombre__iexact=plato.nombre).first()
        categoria_id = request.POST.get('categoria')
        nombre = request.POST.get('nombre', plato.nombre).strip() or plato.nombre
        descripcion = request.POST.get('descripcion', plato.descripcion).strip()
        precio = _to_decimal(request.POST.get('precio'))
        if categoria_id:
            plato.categoria = get_object_or_404(Categoria, pk=categoria_id)
        if insumo_relacionado is not None:
            plato.precio = insumo_relacionado.precio
        elif precio is not None and precio > 0:
            plato.precio = precio
        plato.nombre = nombre
        plato.descripcion = descripcion
        plato.disponible = request.POST.get('disponible') == 'on'
        if request.FILES.get('imagen'):
            plato.imagen = request.FILES['imagen']
        plato.save()
        messages.success(request, f'Plato "{plato.nombre}" actualizado.')
    return redirect('inventario:administrador')


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
        stock_actual = _to_decimal(request.POST.get('stock_actual'), Decimal('0'))
        stock_minimo = _to_decimal(request.POST.get('stock_minimo'), Decimal('0'))
        stock_maximo = _to_decimal(request.POST.get('stock_maximo'), Decimal('0'))
        costo = _to_decimal(request.POST.get('costo'), Decimal('0'))
        precio = _to_decimal(request.POST.get('precio'), Decimal('0'))
        categoria_id = request.POST.get('categoria')
        categoria = None
        if categoria_id:
            categoria = get_object_or_404(Categoria, pk=categoria_id)
        if not nombre:
            messages.error(request, 'El insumo necesita un nombre.')
        elif Insumo.objects.filter(nombre__iexact=nombre).exists():
            messages.error(request, f'Ya existe un insumo llamado "{nombre}".')
        else:
            insumo = Insumo.objects.create(
                nombre=nombre,
                categoria=categoria,
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
            messages.success(request, f'Insumo "{nombre}" creado y sincronizado con la carta.')
    return redirect('inventario:administrador')


@login_required
@user_passes_test(_es_admin)
def insumo_editar(request, pk):
    insumo = get_object_or_404(Insumo, pk=pk)
    if request.method == 'POST':
        insumo.nombre = request.POST.get('nombre', insumo.nombre).strip() or insumo.nombre
        insumo.unidad = request.POST.get('unidad', insumo.unidad)
        stock_actual = _to_decimal(request.POST.get('stock_actual'))
        stock_minimo = _to_decimal(request.POST.get('stock_minimo'))
        stock_maximo = _to_decimal(request.POST.get('stock_maximo'))
        costo = _to_decimal(request.POST.get('costo'))
        precio = _to_decimal(request.POST.get('precio'))
        categoria_id = request.POST.get('categoria')
        if peso := request.POST.get('categoria'):
            insumo.categoria = get_object_or_404(Categoria, pk=peso)
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
    return redirect('inventario:administrador')


@login_required
@user_passes_test(_es_admin)
def movimiento_crear(request):
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
    return redirect('/administrador/#inventario')


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
        insumo = Insumo.objects.filter(pk=insumo_id).first()
        if not insumo:
            messages.error(request, 'Selecciona un insumo válido.')
        elif not cantidad or cantidad <= 0:
            messages.error(request, 'La cantidad debe ser mayor que cero.')
        else:
            RecetaItem.objects.update_or_create(
                plato=plato, insumo=insumo, defaults={'cantidad': cantidad}
            )
            messages.success(request, f'"{insumo.nombre}" agregado a la receta de {plato.nombre}.')
    return redirect(f"/administrador/?plato={plato_id}")


@login_required
@user_passes_test(_es_admin)
def receta_item_eliminar(request, pk):
    item = get_object_or_404(RecetaItem, pk=pk)
    plato_id = item.plato_id
    if request.method == 'POST':
        item.delete()
        messages.success(request, 'Ingrediente quitado de la receta.')
    return redirect(f"/administrador/?plato={plato_id}")


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
