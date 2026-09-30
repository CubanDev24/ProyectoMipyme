from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import logout, login, authenticate, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from carta.models import Plato
from inventario.models import Insumo, MovimientoInventario
from pedidos.models import Factura
from .models import Notificacion, Turno, Usuario, cerrar_turno, configurar_turno, crear_mesas_del_turno, get_turno_abierto


ROLE_HOME = {
    'administrador': 'inventario:administrador',
    'cocina': 'pedidos:cocina',
    'mesera': 'pedidos:mesera',
    'cajera': 'pedidos:caja',
}


def landing(request):
    if request.user.is_authenticated:
        url = ROLE_HOME.get(request.user.role, 'carta:index')
        return redirect(url)
    return render(request, 'usuarios/login.html')


@require_http_methods(['GET', 'POST'])
def login_view(request):
    if request.user.is_authenticated:
        return redirect(ROLE_HOME.get(request.user.role, 'usuarios:dashboard'))

    if request.method == 'GET':
        return redirect('usuarios:landing')

    username = request.POST.get('username', '').strip()
    password = request.POST.get('password', '')
    user = authenticate(request, username=username, password=password)
    if user is None:
        messages.error(request, 'Credenciales inválidas.')
        return redirect('usuarios:landing')

    login(request, user)
    turno = get_turno_abierto()
    if turno:
        turno.usuarios.add(user)
        user.ultimo_login_turno = timezone.now()
        user.save(update_fields=['ultimo_login_turno'])
    return redirect(ROLE_HOME.get(user.role, 'usuarios:landing'))


@login_required
def dashboard(request):
    hoy = timezone.localdate()
    turno = get_turno_abierto()
    turno_anterior = None
    turno_abierto_pendiente = None
    if request.user.role == 'administrador':
        turno = Turno.objects.filter(fecha=hoy).prefetch_related('usuarios').order_by('-apertura').first()
        turno_anterior = Turno.objects.filter(fecha__lt=hoy).prefetch_related('usuarios').order_by('-fecha', '-apertura').first()
        turno_abierto_pendiente = Turno.objects.filter(estado='abierto', fecha__lt=hoy).order_by('-fecha', '-apertura').first()
    notificaciones = Notificacion.objects.filter(destinatario=request.user).order_by('-creada_en')[:10]
    facturas_turno = Factura.objects.none()
    if turno:
        fin_turno = turno.cierre or timezone.now()
        facturas_turno = Factura.objects.filter(
            creado_en__gte=turno.apertura,
            creado_en__lte=fin_turno,
        ).select_related('pedido__mesa').order_by('-creado_en')
    return render(request, 'usuarios/dashboard.html', {
        'turno': turno,
        'hoy': hoy,
        'turno_anterior': turno_anterior,
        'resumen_turno_anterior': turno_anterior.resumen_financiero if turno_anterior else None,
        'resumen_turno': turno.resumen_financiero if turno else None,
        'turno_abierto_pendiente': turno_abierto_pendiente,
        'facturas_turno': facturas_turno,
        'notificaciones': notificaciones,
    })


@login_required
def gestion_usuarios_view(request):
    if request.user.role != 'administrador':
        messages.error(request, 'Solo el administrador puede gestionar usuarios.')
        return redirect('usuarios:dashboard')
    return render(request, 'usuarios/gestion_usuarios.html', {
        'usuarios': Usuario.objects.order_by('role', 'username'),
    })


@login_required
@require_http_methods(['POST'])
def abrir_turno_view(request):
    if request.user.role != 'administrador':
        messages.error(request, 'Solo el administrador puede abrir el turno.')
        return redirect('usuarios:dashboard')

    hoy = timezone.localdate()
    if Turno.objects.filter(fecha=hoy).exists():
        messages.error(request, 'Ya existe un turno registrado para hoy.')
        return redirect('usuarios:dashboard')
    if Turno.objects.filter(estado='abierto').exists():
        messages.error(request, 'Hay un turno anterior abierto. Debe cerrarse antes de abrir uno nuevo.')
        return redirect('usuarios:dashboard')

    turno = Turno.objects.create(fecha=hoy, estado='abierto')
    turno.usuarios.add(request.user)
    request.user.ultimo_login_turno = timezone.now()
    request.user.save(update_fields=['ultimo_login_turno'])
    crear_mesas_del_turno(turno)
    messages.success(request, f'Turno del {hoy:%d/%m/%Y} abierto correctamente.')
    return redirect('usuarios:dashboard')


def _decimal_o_cero(valor):
    try:
        return Decimal(str(valor).replace(',', '.').strip())
    except (ArithmeticError, TypeError, ValueError, AttributeError):
        return Decimal('0')


@login_required
def inventario_ventas_turno(request):
    turno = get_turno_abierto()
    if turno is None:
        return JsonResponse({
            'turno_abierto': False,
            'productos': [],
            'actualizado_en': timezone.localtime().strftime('%H:%M:%S'),
        })

    ahora = timezone.now()
    insumos = list(Insumo.objects.filter(activo=True).select_related('categoria').order_by('nombre'))
    insumo_ids = [insumo.pk for insumo in insumos]
    movimientos = MovimientoInventario.objects.filter(
        insumo_id__in=insumo_ids,
        creado_en__lte=ahora,
    ).only(
        'insumo_id', 'tipo', 'cantidad', 'stock_anterior', 'stock_posterior', 'motivo', 'creado_en',
    ).order_by('insumo_id', 'creado_en', 'pk')

    saldo_previo = {}
    saldo_apertura = {}
    movimientos_turno = {}
    for movimiento in movimientos:
        insumo_id = movimiento.insumo_id
        if movimiento.creado_en < turno.apertura:
            saldo_previo[insumo_id] = movimiento.stock_posterior
            continue
        saldo_apertura.setdefault(insumo_id, movimiento.stock_anterior)
        datos = movimientos_turno.setdefault(insumo_id, {
            'entradas': Decimal('0'),
            'salidas': Decimal('0'),
            'merma': Decimal('0'),
        })
        if movimiento.tipo == 'entrada':
            datos['entradas'] += movimiento.cantidad
        elif movimiento.tipo == 'merma':
            datos['merma'] += movimiento.cantidad
            datos['salidas'] += movimiento.cantidad
        elif movimiento.tipo == 'salida':
            datos['salidas'] += movimiento.cantidad
            if 'rotura' in (movimiento.motivo or '').casefold():
                datos['merma'] += movimiento.cantidad

    insumo_por_nombre = {insumo.nombre.strip().casefold(): insumo.pk for insumo in insumos}
    ventas = {}
    facturas = Factura.objects.filter(
        creado_en__gte=turno.apertura,
        creado_en__lte=ahora,
    ).values_list('items_snapshot', flat=True)
    for snapshot in facturas:
        for item in snapshot or []:
            nombre = str(item.get('plato', '')).strip().casefold()
            if not nombre:
                continue
            datos = ventas.setdefault(nombre, {
                'nombre': str(item.get('plato', '')).strip(),
                'cantidad': Decimal('0'),
                'importe': Decimal('0'),
                'precio_total': Decimal('0'),
            })
            cantidad = _decimal_o_cero(item.get('cantidad'))
            datos['cantidad'] += cantidad
            datos['importe'] += _decimal_o_cero(item.get('subtotal'))
            datos['precio_total'] += _decimal_o_cero(item.get('precio_unit')) * cantidad

    platos_por_nombre = {
        plato.nombre.strip().casefold(): plato
        for plato in Plato.objects.select_related('categoria').all()
    }
    productos = []
    for insumo in insumos:
        mov = movimientos_turno.get(insumo.pk, {})
        nombre_key = insumo.nombre.strip().casefold()
        venta = ventas.get(nombre_key, {})
        inicial = saldo_apertura.get(insumo.pk, saldo_previo.get(insumo.pk, insumo.stock_actual))
        entradas = mov.get('entradas', Decimal('0'))
        salidas = mov.get('salidas', Decimal('0'))
        merma = mov.get('merma', Decimal('0'))
        productos.append({
            'nombre': insumo.nombre,
            'categoria': insumo.categoria.nombre if insumo.categoria else 'Sin categoría',
            'unidad': insumo.get_unidad_display(),
            'saldo_inicial': str(inicial),
            'entradas': str(entradas),
            'cantidad_existencia': str(insumo.stock_actual),
            'cantidad_vendida': str(venta.get('cantidad', Decimal('0'))),
            'saldo_final': str(inicial + entradas - salidas),
            'precio_venta': str(insumo.precio),
            'importe_venta': str(venta.get('importe', Decimal('0'))),
            'merma_rotura': str(merma),
            'control_existencia': True,
        })

    for nombre_key, venta in ventas.items():
        if nombre_key in insumo_por_nombre:
            continue
        plato = platos_por_nombre.get(nombre_key)
        cantidad = venta['cantidad']
        precio_venta = plato.precio if plato else (
            venta['precio_total'] / cantidad if cantidad else Decimal('0')
        )
        productos.append({
            'nombre': venta['nombre'],
            'categoria': plato.categoria.nombre if plato and plato.categoria else 'Sin categoría',
            'unidad': 'Unidad',
            'saldo_inicial': None,
            'entradas': None,
            'cantidad_existencia': None,
            'cantidad_vendida': str(cantidad),
            'saldo_final': None,
            'precio_venta': str(precio_venta),
            'importe_venta': str(venta['importe']),
            'merma_rotura': None,
            'control_existencia': False,
        })
    productos.sort(key=lambda producto: producto['nombre'].casefold())

    return JsonResponse({
        'turno_abierto': True,
        'actualizado_en': timezone.localtime(ahora).strftime('%H:%M:%S'),
        'productos': productos,
    })


@login_required
def logout_view(request):
    logout(request)
    return redirect('usuarios:landing')


@login_required
@require_http_methods(['POST'])
def crear_usuario(request):
    if request.user.role != 'administrador':
        messages.error(request, 'Solo el administrador puede crear usuarios.')
        return redirect('usuarios:dashboard')

    username = request.POST.get('username', '').strip()
    first_name = request.POST.get('first_name', '').strip()
    last_name = request.POST.get('last_name', '').strip()
    password = request.POST.get('password', '')
    role = request.POST.get('role')
    if not username or not first_name or not last_name or not password or role not in dict(Usuario.ROLE_CHOICES):
        messages.error(request, 'Completa los datos del usuario.')
        return redirect('usuarios:gestion_usuarios')

    if Usuario.objects.filter(username__iexact=username).exists():
        messages.error(request, f'El usuario {username} ya existe.')
        return redirect('usuarios:gestion_usuarios')

    Usuario.objects.create_user(
        username=username,
        first_name=first_name,
        last_name=last_name,
        password=password,
        role=role,
        is_active=True,
        activo=True,
    )
    messages.success(request, f'Usuario {first_name} {last_name} creado con rol {dict(Usuario.ROLE_CHOICES)[role]}.')
    return redirect('usuarios:gestion_usuarios')


@login_required
@require_http_methods(['POST'])
def cambiar_estado_usuario(request, user_id):
    if request.user.role != 'administrador':
        messages.error(request, 'Solo el administrador puede gestionar usuarios.')
        return redirect('usuarios:gestion_usuarios')

    usuario = get_object_or_404(Usuario, pk=user_id)
    if usuario == request.user:
        messages.error(request, 'No puedes desactivar tu propio usuario.')
        return redirect('usuarios:gestion_usuarios')

    usuario.is_active = not usuario.is_active
    usuario.activo = usuario.is_active
    usuario.save(update_fields=['is_active', 'activo'])
    estado = 'activado' if usuario.is_active else 'desactivado'
    messages.success(request, f'Usuario {usuario.username} {estado}.')
    return redirect('usuarios:gestion_usuarios')


@login_required
@require_http_methods(['POST'])
def editar_usuario(request, user_id):
    if request.user.role != 'administrador':
        messages.error(request, 'Solo el administrador puede gestionar usuarios.')
        return redirect('usuarios:gestion_usuarios')

    usuario = get_object_or_404(Usuario, pk=user_id)
    username = request.POST.get('username', '').strip()
    first_name = request.POST.get('first_name', '').strip()
    last_name = request.POST.get('last_name', '').strip()
    role = request.POST.get('role')
    password = request.POST.get('password', '')
    if not username or not first_name or not last_name or role not in dict(Usuario.ROLE_CHOICES):
        messages.error(request, 'Completa nombre, apellidos, usuario y rol.')
        return redirect('usuarios:gestion_usuarios')

    if Usuario.objects.filter(username__iexact=username).exclude(pk=usuario.pk).exists():
        messages.error(request, f'El usuario {username} ya existe.')
        return redirect('usuarios:gestion_usuarios')
    if usuario == request.user and role != 'administrador':
        messages.error(request, 'No puedes quitarte el rol de administrador a ti mismo.')
        return redirect('usuarios:gestion_usuarios')

    usuario.username = username
    usuario.first_name = first_name
    usuario.last_name = last_name
    usuario.email = request.POST.get('email', '').strip()
    usuario.telefono = request.POST.get('telefono', '').strip()
    usuario.role = role
    if password:
        usuario.set_password(password)
    usuario.save()
    if usuario == request.user and password:
        update_session_auth_hash(request, usuario)
    messages.success(request, f'Usuario {usuario.get_full_name()} actualizado correctamente.')
    return redirect('usuarios:gestion_usuarios')


@login_required
@require_http_methods(['POST'])
def configurar_mesas_turno_view(request):
    if request.user.role != 'administrador':
        messages.error(request, 'Solo el administrador puede configurar las mesas del turno.')
        return redirect('usuarios:dashboard')

    turno = get_turno_abierto()
    if turno is None:
        messages.error(request, 'Primero debes abrir el turno del día.')
        return redirect('usuarios:dashboard')

    cantidad_mesas = request.POST.get('cantidad_mesas', '').strip()
    if not cantidad_mesas:
        messages.error(request, 'Debes indicar la cantidad de mesas.')
        return redirect('usuarios:dashboard')

    try:
        cantidad = int(cantidad_mesas)
    except ValueError:
        messages.error(request, 'La cantidad de mesas debe ser un número entero.')
        return redirect('usuarios:dashboard')

    turno.cantidad_mesas = max(cantidad, 1)
    turno.save(update_fields=['cantidad_mesas'])
    from usuarios.models import crear_mesas_del_turno
    crear_mesas_del_turno(turno)
    messages.success(request, f'Cantidad de mesas del turno actualizada a {turno.cantidad_mesas}.')
    return redirect('usuarios:dashboard')


@login_required
@require_http_methods(['POST'])
def cerrar_turno_view(request):
    if request.user.role != 'cajera':
        messages.error(request, 'Solo la cajera puede cerrar el turno.')
        return redirect('usuarios:dashboard')

    try:
        turno = cerrar_turno(request.user, observaciones=request.POST.get('observaciones', ''))
        messages.success(request, f'Turno cerrado correctamente. Resumen: {turno.resumen}')
    except PermissionError as exc:
        messages.error(request, str(exc))
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect('usuarios:dashboard')


@login_required
def historial_turnos_view(request):
    if request.user.role != 'administrador':
        messages.error(request, 'Solo el administrador puede ver el historial de turnos.')
        return redirect('usuarios:dashboard')

    turnos = Turno.objects.filter(estado='cerrado').prefetch_related('usuarios').order_by('-cierre', '-fecha')
    historial = []
    total_general = Decimal('0')
    total_efectivo = Decimal('0')
    total_transferencia = Decimal('0')
    total_usd = Decimal('0')
    total_facturas = 0
    total_horas = Decimal('0')

    for turno in turnos:
        data = turno.resumen_financiero
        historial.append({'turno': turno, **data})
        total_general += data['total_general_cup']
        total_efectivo += data['total_efectivo_cup']
        total_transferencia += data['total_transferencia_cup']
        total_usd += data['total_usd']
        total_facturas += data['cantidad_facturas']
        total_horas += data['horas_abiertas']

    promedio_ipv = (total_general / Decimal(total_facturas)) if total_facturas else Decimal('0')
    rendimiento_promedio = (total_general / total_horas) if total_horas > 0 else Decimal('0')

    context = {
        'historial': historial,
        'resumen_general': {
            'total_general_cup': total_general,
            'total_efectivo_cup': total_efectivo,
            'total_transferencia_cup': total_transferencia,
            'total_usd': total_usd,
            'total_facturas': total_facturas,
            'promedio_ipv': promedio_ipv,
            'rendimiento_promedio': rendimiento_promedio,
            'turnos_cerrados': turnos.count(),
        },
    }
    return render(request, 'usuarios/historial_turnos.html', context)


@login_required
def historial_turno_detalle_view(request, turno_id):
    if request.user.role != 'administrador':
        messages.error(request, 'Solo el administrador puede ver el detalle de un turno.')
        return redirect('usuarios:dashboard')

    turno = get_object_or_404(Turno.objects.prefetch_related('usuarios'), pk=turno_id)
    data = turno.resumen_financiero
    context = {
        'turno': turno,
        'detalles': data,
        'resumen': turno.resumen or 'Sin resumen registrado.',
    }
    return render(request, 'usuarios/historial_turno_detalle.html', context)


@login_required
def ipv_turno(request, turno_id):
    """
    Devuelve el inventario perpetuo de ventas (IPV) de un turno concreto.
    Funciona tanto para turnos cerrados (histórico) como para el turno abierto (tiempo real).
    """
    if request.user.role != 'administrador':
        return JsonResponse({'error': 'Sin permiso'}, status=403)

    turno = get_object_or_404(Turno, pk=turno_id)

    apertura = turno.apertura
    # Para turno cerrado usamos el cierre exacto; para el abierto, ahora mismo.
    cierre = turno.cierre if turno.estado == 'cerrado' else timezone.now()

    # ── Movimientos de inventario ──────────────────────────────────────────────
    insumos = list(
        Insumo.objects.filter(activo=True)
        .select_related('categoria')
        .order_by('nombre')
    )
    insumo_ids = [insumo.pk for insumo in insumos]

    movimientos = (
        MovimientoInventario.objects.filter(
            insumo_id__in=insumo_ids,
            creado_en__lte=cierre,
        )
        .only(
            'insumo_id', 'tipo', 'cantidad',
            'stock_anterior', 'stock_posterior', 'motivo', 'creado_en',
        )
        .order_by('insumo_id', 'creado_en', 'pk')
    )

    existencia_por_insumo = {}
    movimientos_turno = {}

    for movimiento in movimientos:
        iid = movimiento.insumo_id
        existencia_por_insumo[iid] = movimiento.stock_posterior
        if movimiento.creado_en < apertura:
            continue
        datos = movimientos_turno.setdefault(iid, {
            'entradas': Decimal('0'),
            'salidas': Decimal('0'),
            'merma': Decimal('0'),
        })
        if movimiento.tipo == 'entrada':
            datos['entradas'] += movimiento.cantidad
        elif movimiento.tipo == 'merma':
            datos['merma'] += movimiento.cantidad
            datos['salidas'] += movimiento.cantidad
        elif movimiento.tipo == 'salida':
            datos['salidas'] += movimiento.cantidad
            if 'rotura' in (movimiento.motivo or '').casefold():
                datos['merma'] += movimiento.cantidad

    # ── Ventas de facturas ─────────────────────────────────────────────────────
    insumo_por_nombre = {
        insumo.nombre.strip().casefold(): insumo.pk for insumo in insumos
    }
    ventas = {}
    facturas = Factura.objects.filter(
        creado_en__gte=apertura,
        creado_en__lte=cierre,
    ).values_list('items_snapshot', flat=True)

    for snapshot in facturas:
        for item in snapshot or []:
            nombre = str(item.get('plato', '')).strip().casefold()
            if not nombre:
                continue
            datos = ventas.setdefault(nombre, {
                'nombre': str(item.get('plato', '')).strip(),
                'cantidad': Decimal('0'),
                'importe': Decimal('0'),
                'precio_total_acum': Decimal('0'),
            })
            cantidad = _decimal_o_cero(item.get('cantidad'))
            datos['cantidad'] += cantidad
            datos['importe'] += _decimal_o_cero(item.get('subtotal'))
            datos['precio_total_acum'] += _decimal_o_cero(item.get('precio_unit')) * cantidad

    from carta.models import Plato
    platos_por_nombre = {
        plato.nombre.strip().casefold(): plato
        for plato in Plato.objects.select_related('categoria').all()
    }

    # ── Construcción de filas del IPV ──────────────────────────────────────────
    filas = []
    total_importe = Decimal('0')

    for insumo in insumos:
        mov = movimientos_turno.get(insumo.pk, {})
        nombre_key = insumo.nombre.strip().casefold()
        venta = ventas.get(nombre_key, {})
        entradas = mov.get('entradas', Decimal('0'))
        salidas = mov.get('salidas', Decimal('0'))
        merma = mov.get('merma', Decimal('0'))
        cantidad_vendida = venta.get('cantidad', Decimal('0'))
        importe_venta = venta.get('importe', Decimal('0'))
        existencia = existencia_por_insumo.get(insumo.pk, insumo.stock_actual)

        total_importe += importe_venta

        filas.append({
            'nombre': insumo.nombre,
            'categoria': insumo.categoria.nombre if insumo.categoria else 'Sin categoría',
            'unidad': insumo.get_unidad_display(),
            'saldo_inicial': None,
            'entradas': str(entradas),
            'cantidad_existencia': str(existencia),
            'cantidad_vendida': str(cantidad_vendida),
            'saldo_final': None,
            'precio_venta': str(insumo.precio),
            'importe_venta': str(importe_venta),
            'merma_rotura': str(merma),
            'control_existencia': True,
        })

    # Productos vendidos sin insumo en inventario (platos sin stock tracking)
    for nombre_key, venta in ventas.items():
        if nombre_key in insumo_por_nombre:
            continue
        plato = platos_por_nombre.get(nombre_key)
        cantidad = venta['cantidad']
        precio_venta = plato.precio if plato else (
            venta['precio_total_acum'] / cantidad if cantidad else Decimal('0')
        )
        importe_venta = venta['importe']
        total_importe += importe_venta
        filas.append({
            'nombre': venta['nombre'],
            'categoria': plato.categoria.nombre if plato and plato.categoria else 'Sin categoría',
            'unidad': 'Unidad',
            'saldo_inicial': None,
            'entradas': None,
            'cantidad_existencia': None,
            'cantidad_vendida': str(cantidad),
            'saldo_final': None,
            'precio_venta': str(precio_venta),
            'importe_venta': str(importe_venta),
            'merma_rotura': None,
            'control_existencia': False,
        })

    filas.sort(key=lambda f: f['nombre'].casefold())

    return JsonResponse({
        'turno_id': turno_id,
        'estado': turno.estado,
        'fecha': turno.fecha.strftime('%d/%m/%Y'),
        'actualizado_en': timezone.localtime(cierre).strftime('%H:%M:%S'),
        'total_importe': str(total_importe),
        'filas': filas,
    })
