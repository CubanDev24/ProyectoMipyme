from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from django.contrib import messages
from django.contrib.auth import logout, login, authenticate, get_user, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Max, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from django.views.csrf import csrf_failure as django_csrf_failure
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from carta.models import Plato
from carta.qr import generar_qr_mesa
from inventario.models import Insumo, MovimientoInventario
from pedidos.models import Factura, Mesa, Pedido
from .models import Notificacion, Turno, Usuario, cerrar_turno, configurar_turno, crear_mesas_del_turno, get_turno_abierto


ROLE_HOME = {
    'administrador': 'inventario:administrador',
    'cocina': 'pedidos:cocina',
    'mesera': 'pedidos:mesera',
    'cajera': 'pedidos:caja',
}
ROLES_CON_ACCESO_FINANCIERO = {'administrador', 'cajera'}


@never_cache
def csrf_failure(request, reason=''):
    if request.path != reverse('usuarios:login') or request.method != 'POST':
        return django_csrf_failure(request, reason=reason)

    user = get_user(request)
    autenticado = user.is_authenticated
    if autenticado:
        destino = reverse(ROLE_HOME.get(user.role, 'usuarios:dashboard'))
    else:
        destino = reverse('usuarios:landing')
    return render(request, 'usuarios/csrf_failure.html', {
        'autenticado': autenticado,
        'destino': destino,
    }, status=403)


@never_cache
def landing(request):
    if request.user.is_authenticated:
        url = ROLE_HOME.get(request.user.role, 'carta:index')
        return redirect(url)
    return render(request, 'usuarios/login.html')


@never_cache
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
    puede_ver_datos_financieros = request.user.role in ROLES_CON_ACCESO_FINANCIERO
    turno = get_turno_abierto()
    turno_anterior = None
    turno_abierto_pendiente = None
    if request.user.role == 'administrador':
        turno = Turno.objects.filter(fecha=hoy).prefetch_related('usuarios').order_by('-apertura').first()
        turno_anterior = Turno.objects.filter(fecha__lt=hoy).prefetch_related('usuarios').order_by('-fecha', '-apertura').first()
        turno_abierto_pendiente = Turno.objects.filter(estado='abierto', fecha__lt=hoy).order_by('-fecha', '-apertura').first()
    mesas_admin = []
    mesas_activas_salon = 0
    mesas_activas_exteriores = 0
    if turno:
        if request.user.role == 'administrador' and turno.estado == 'abierto':
            crear_mesas_del_turno(turno)
        mesas_activas = Mesa.objects.filter(activa=True).values_list('zona', flat=True)
        for zona in mesas_activas:
            if zona == 'exteriores':
                mesas_activas_exteriores += 1
            else:
                mesas_activas_salon += 1
        if request.user.role == 'administrador' and turno.estado == 'abierto':
            mesas_admin = list(Mesa.objects.order_by('zona', 'numero_zona', 'numero'))
        for mesa in mesas_admin:
            mesa.qr_url, mesa.qr_data = generar_qr_mesa(request, mesa)
    notificaciones = Notificacion.objects.filter(destinatario=request.user).order_by('-creada_en')[:10]
    facturas_turno = Factura.objects.none()
    if turno and puede_ver_datos_financieros:
        fin_turno = turno.cierre or timezone.now()
        facturas_turno = Factura.objects.filter(
            creado_en__gte=turno.apertura,
            creado_en__lte=fin_turno,
        ).select_related('pedido__mesa').order_by('-creado_en')
    return render(request, 'usuarios/dashboard.html', {
        'turno': turno,
        'hoy': hoy,
        'turno_anterior': turno_anterior,
        'resumen_turno_anterior': turno_anterior.resumen_financiero if turno_anterior and puede_ver_datos_financieros else None,
        'resumen_turno': turno.resumen_financiero if turno and puede_ver_datos_financieros else None,
        'puede_ver_datos_financieros': puede_ver_datos_financieros,
        'turno_abierto_pendiente': turno_abierto_pendiente,
        'mesas_admin': mesas_admin,
        'mesas_activas_salon': mesas_activas_salon,
        'mesas_activas_exteriores': mesas_activas_exteriores,
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
    if request.user.role not in ROLES_CON_ACCESO_FINANCIERO:
        return JsonResponse({'error': 'Sin permiso'}, status=403)

    turno = get_turno_abierto()
    if turno is None:
        return JsonResponse({
            'turno_abierto': False,
            'productos': [],
            'actualizado_en': timezone.localtime().strftime('%H:%M:%S'),
        })

    ahora = timezone.now()
    insumos = list(
        Insumo.objects.filter(activo=True)
        .select_related('categoria', 'categoria_inventario')
        .order_by('nombre')
    )
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
            'area': insumo.get_tipo_inventario_display() if insumo.tipo_inventario else 'Por clasificar',
            'categoria': (
                insumo.categoria_inventario.nombre if insumo.categoria_inventario
                else insumo.categoria.nombre if insumo.categoria
                else 'Sin categoría'
            ),
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
    cantidad_mesas_exteriores = request.POST.get('cantidad_mesas_exteriores', '0').strip()
    if not cantidad_mesas:
        messages.error(request, 'Debes indicar la cantidad de mesas.')
        return redirect('usuarios:dashboard')

    try:
        cantidad = int(cantidad_mesas)
        cantidad_exteriores = int(cantidad_mesas_exteriores)
    except ValueError:
        messages.error(request, 'Las cantidades de mesas deben ser números enteros.')
        return redirect('usuarios:dashboard')

    if cantidad_exteriores < 0:
        messages.error(request, 'La cantidad de mesas exteriores no puede ser negativa.')
        return redirect('usuarios:dashboard')

    turno.cantidad_mesas = max(cantidad, 1)
    turno.cantidad_mesas_exteriores = cantidad_exteriores
    turno.save(update_fields=['cantidad_mesas', 'cantidad_mesas_exteriores'])
    from usuarios.models import crear_mesas_del_turno
    crear_mesas_del_turno(turno)
    messages.success(
        request,
        f'Mesas actualizadas: {turno.cantidad_mesas} de salón y '
        f'{turno.cantidad_mesas_exteriores} exteriores.',
    )
    return redirect('usuarios:dashboard')


@login_required
@require_http_methods(['POST'])
def agregar_mesa_turno_view(request):
    if request.user.role != 'administrador':
        messages.error(request, 'Solo el administrador puede gestionar las mesas del turno.')
        return redirect('usuarios:dashboard')

    turno = get_turno_abierto()
    if turno is None:
        messages.error(request, 'Primero debes abrir el turno del día.')
        return redirect('usuarios:dashboard')

    zona = request.POST.get('zona', '')
    if zona not in ('salon', 'exteriores'):
        messages.error(request, 'Selecciona una zona válida para la mesa.')
        return redirect('usuarios:dashboard')

    siguiente_numero_zona = (
        Mesa.objects.filter(zona=zona).aggregate(
            ultimo=Max(Coalesce('numero_zona', 'numero')),
        )['ultimo'] or 0
    ) + 1
    siguiente_numero = (Mesa.objects.aggregate(ultimo=Max('numero'))['ultimo'] or 0) + 1
    mesa = Mesa.objects.create(
        numero=siguiente_numero,
        zona=zona,
        numero_zona=siguiente_numero_zona,
        activa=True,
        abierta=False,
    )
    if zona == 'exteriores':
        turno.cantidad_mesas_exteriores = max(turno.cantidad_mesas_exteriores, siguiente_numero_zona)
        turno.save(update_fields=['cantidad_mesas_exteriores'])
    else:
        turno.cantidad_mesas = max(turno.cantidad_mesas, siguiente_numero_zona)
        turno.save(update_fields=['cantidad_mesas'])
    messages.success(request, f'{mesa.etiqueta} agregada y activada.')
    return redirect('usuarios:dashboard')


@login_required
@require_http_methods(['POST'])
def cambiar_estado_mesa_turno_view(request, mesa_id):
    if request.user.role != 'administrador':
        messages.error(request, 'Solo el administrador puede gestionar las mesas del turno.')
        return redirect('usuarios:dashboard')

    if get_turno_abierto() is None:
        messages.error(request, 'Primero debes abrir el turno del día.')
        return redirect('usuarios:dashboard')

    mesa = get_object_or_404(Mesa, pk=mesa_id)
    estado = request.POST.get('activa')
    if estado not in ('0', '1'):
        messages.error(request, 'El estado indicado para la mesa no es válido.')
        return redirect('usuarios:dashboard')

    activar = estado == '1'
    if not activar and (mesa.abierta or Pedido.objects.filter(mesa=mesa).exclude(estado='cerrado').exists()):
        messages.error(request, f'No se puede desactivar {mesa.etiqueta} mientras tenga una cuenta abierta.')
        return redirect('usuarios:dashboard')

    mesa.activa = activar
    mesa.save(update_fields=['activa'])
    estado_texto = 'activada' if activar else 'desactivada'
    messages.success(request, f'{mesa.etiqueta} {estado_texto}.')
    return redirect('usuarios:dashboard')


@login_required
@require_http_methods(['POST'])
def cerrar_turno_view(request):
    if request.user.role not in ('cajera', 'administrador'):
        messages.error(request, 'Solo caja o el administrador pueden cerrar el turno.')
        return redirect('usuarios:dashboard')

    try:
        turno_pendiente = None
        turno_id = request.POST.get('turno_id')
        if turno_id and request.user.role != 'administrador':
            messages.error(request, 'Solo el administrador puede cerrar un turno anterior.')
            return redirect('usuarios:dashboard')
        if turno_id:
            turno_pendiente = get_object_or_404(
                Turno,
                pk=turno_id,
                estado='abierto',
                fecha__lt=timezone.localdate(),
            )
        turno = cerrar_turno(
            request.user,
            observaciones=request.POST.get('observaciones', ''),
            turno=turno_pendiente,
        )
        messages.success(request, f'Turno cerrado correctamente. Resumen: {turno.resumen}')
        if request.user.role in ROLES_CON_ACCESO_FINANCIERO:
            return redirect('usuarios:historial_turno_detalle', turno_id=turno.pk)
    except PermissionError as exc:
        messages.error(request, str(exc))
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect('usuarios:dashboard')


@login_required
def historial_turnos_view(request):
    if request.user.role not in ROLES_CON_ACCESO_FINANCIERO:
        messages.error(request, 'Solo el administrador y la cajera pueden ver el historial de turnos.')
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
    turnos_paginados = Paginator(historial, 10).get_page(request.GET.get('page'))
    pagination_query = request.GET.copy()
    pagination_query.pop('page', None)

    context = {
        'historial': turnos_paginados,
        'historial_pagination_query': pagination_query.urlencode(),
        'resumen_general': {
            'total_general_cup': total_general,
            'total_efectivo_cup': total_efectivo,
            'total_transferencia_cup': total_transferencia,
            'total_usd': total_usd,
            'total_facturas': total_facturas,
            'promedio_ipv': promedio_ipv,
            'rendimiento_promedio': rendimiento_promedio,
            'turnos_cerrados': len(historial),
        },
    }
    return render(request, 'usuarios/historial_turnos.html', context)


@login_required
def historial_turno_detalle_view(request, turno_id):
    if request.user.role not in ROLES_CON_ACCESO_FINANCIERO:
        messages.error(request, 'Solo el administrador y la cajera pueden ver el detalle de un turno.')
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
    if request.user.role not in ROLES_CON_ACCESO_FINANCIERO:
        return JsonResponse({'error': 'Sin permiso'}, status=403)

    turno = get_object_or_404(Turno, pk=turno_id)
    return JsonResponse(_datos_ipv_turno(turno))


def _datos_ipv_turno(turno):
    apertura = turno.apertura
    # Para turno cerrado usamos el cierre exacto; para el abierto, ahora mismo.
    cierre = turno.cierre if turno.estado == 'cerrado' else timezone.now()

    # ── Movimientos de inventario ──────────────────────────────────────────────
    insumos = list(
        Insumo.objects.filter(activo=True)
        .select_related('categoria', 'categoria_inventario')
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
            'area': insumo.get_tipo_inventario_display() if insumo.tipo_inventario else 'Por clasificar',
            'categoria': (
                insumo.categoria_inventario.nombre if insumo.categoria_inventario
                else insumo.categoria.nombre if insumo.categoria
                else 'Sin categoría'
            ),
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

    return {
        'turno_id': turno.pk,
        'estado': turno.estado,
        'fecha': turno.fecha.strftime('%d/%m/%Y'),
        'actualizado_en': timezone.localtime(cierre).strftime('%H:%M:%S'),
        'total_importe': str(total_importe),
        'filas': filas,
    }


IPV_COLUMNAS = (
    ('Descripción', 'nombre'),
    ('Categoría', 'categoria'),
    ('Saldo inicial', 'saldo_inicial'),
    ('Entradas', 'entradas'),
    ('En existencia', 'cantidad_existencia'),
    ('Saldo final', 'saldo_final'),
    ('Cant. vendida', 'cantidad_vendida'),
    ('Precio venta', 'precio_venta'),
    ('Importe', 'importe_venta'),
    ('Merma / rotura', 'merma_rotura'),
)


@login_required
def ipv_turno_pdf(request, turno_id):
    if request.user.role not in ROLES_CON_ACCESO_FINANCIERO:
        return JsonResponse({'error': 'Sin permiso'}, status=403)

    turno = get_object_or_404(Turno, pk=turno_id)
    datos = _datos_ipv_turno(turno)
    buffer = BytesIO()
    pagina = landscape(A4)
    documento = SimpleDocTemplate(
        buffer,
        pagesize=pagina,
        leftMargin=10 * mm,
        rightMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
        title=f"IPV turno {turno.pk}",
    )
    estilos = getSampleStyleSheet()
    estilo_celda = ParagraphStyle(
        'IPVCelda',
        parent=estilos['BodyText'],
        fontName='Helvetica',
        fontSize=6,
        leading=7,
        spaceAfter=0,
    )
    estilo_numero = ParagraphStyle(
        'IPVNumero',
        parent=estilo_celda,
        alignment=TA_RIGHT,
    )
    estilo_encabezado = ParagraphStyle(
        'IPVEncabezado',
        parent=estilo_celda,
        fontName='Helvetica-Bold',
        textColor=colors.white,
    )
    encabezados = [Paragraph(escape(titulo), estilo_encabezado) for titulo, _ in IPV_COLUMNAS]
    filas_tabla = [encabezados]
    for fila in datos['filas']:
        filas_tabla.append([
            Paragraph(
                escape(str(fila[clave]) if fila[clave] is not None else '—'),
                estilo_celda if indice < 2 else estilo_numero,
            )
            for indice, (_, clave) in enumerate(IPV_COLUMNAS)
        ])
    filas_tabla.append([
        Paragraph('Total importe de ventas', estilo_celda),
        '', '', '', '', '', '', '',
        Paragraph(escape(datos['total_importe']), estilo_numero),
        '',
    ])
    ancho_disponible = pagina[0] - 20 * mm
    proporciones = (0.19, 0.12, 0.08, 0.08, 0.10, 0.08, 0.10, 0.09, 0.09, 0.07)
    tabla = Table(
        filas_tabla,
        colWidths=[ancho_disponible * proporcion for proporcion in proporciones],
        repeatRows=1,
    )
    estilos_tabla = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e293b')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#e2e8f0')),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('SPAN', (0, -1), (7, -1)),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#cbd5e1')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (2, 1), (-1, -1), 'RIGHT'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]
    if datos['filas']:
        estilos_tabla.append(('BACKGROUND', (0, 1), (-1, -2), colors.HexColor('#f8fafc')))
        estilos_tabla.append(
            ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#f1f5f9')]),
        )
    tabla.setStyle(TableStyle(estilos_tabla))
    contenido = [
        Paragraph('Inventario Perpetuo de Ventas (IPV)', estilos['Title']),
        Paragraph(
            f"Turno {escape(datos['fecha'])} · "
            f"{'Abierto' if turno.estado == 'abierto' else 'Cerrado'} · "
            f"Actualizado {escape(datos['actualizado_en'])}",
            estilos['Normal'],
        ),
        Spacer(1, 8 * mm),
        tabla,
    ]
    documento.build(contenido)
    respuesta = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    respuesta['Content-Disposition'] = f'attachment; filename="ipv_turno_{turno.pk}.pdf"'
    return respuesta


@login_required
def ipv_turno_excel(request, turno_id):
    if request.user.role not in ROLES_CON_ACCESO_FINANCIERO:
        return JsonResponse({'error': 'Sin permiso'}, status=403)

    turno = get_object_or_404(Turno, pk=turno_id)
    datos = _datos_ipv_turno(turno)
    libro = Workbook()
    hoja = libro.active
    hoja.title = 'IPV'
    hoja.append(['Inventario Perpetuo de Ventas (IPV)'])
    hoja.append([
        'Turno',
        datos['fecha'],
        'Estado',
        'Abierto' if turno.estado == 'abierto' else 'Cerrado',
        'Actualizado',
        datos['actualizado_en'],
    ])
    hoja.append([])
    hoja.append([titulo for titulo, _ in IPV_COLUMNAS])
    for celda in hoja[4]:
        celda.font = Font(bold=True, color='FFFFFF')
        celda.fill = PatternFill('solid', fgColor='1E293B')
        celda.alignment = Alignment(horizontal='center', wrap_text=True)

    for fila in datos['filas']:
        valores = []
        for _, clave in IPV_COLUMNAS:
            valor = fila[clave]
            if valor is None:
                valores.append(None)
            elif clave in {
                'saldo_inicial',
                'entradas',
                'cantidad_existencia',
                'saldo_final',
                'cantidad_vendida',
                'precio_venta',
                'importe_venta',
                'merma_rotura',
            }:
                valores.append(Decimal(valor))
            else:
                valores.append(str(valor))
        hoja.append(valores)

    fila_total = hoja.max_row + 1
    hoja.cell(row=fila_total, column=1, value='Total importe de ventas')
    hoja.cell(row=fila_total, column=9, value=Decimal(datos['total_importe']))
    for celda in hoja[fila_total]:
        celda.font = Font(bold=True)
        celda.fill = PatternFill('solid', fgColor='E2E8F0')

    hoja.freeze_panes = 'A5'
    hoja.auto_filter.ref = f'A4:J{fila_total - 1}'
    anchos = (32, 22, 16, 14, 16, 14, 16, 16, 16, 18)
    for indice, ancho in enumerate(anchos, start=1):
        hoja.column_dimensions[chr(64 + indice)].width = ancho
    for fila in hoja.iter_rows(min_row=5, max_row=fila_total):
        for indice in range(3, 11):
            celda = fila[indice - 1]
            if celda.value is not None:
                celda.number_format = '#,##0.00'
        for indice in (1, 2):
            celda = fila[indice - 1]
            if celda.value is not None:
                celda.data_type = 's'

    buffer = BytesIO()
    libro.save(buffer)
    respuesta = HttpResponse(
        buffer.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    respuesta['Content-Disposition'] = f'attachment; filename="ipv_turno_{turno.pk}.xlsx"'
    return respuesta
