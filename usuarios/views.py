from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO
from xml.sax.saxutils import escape

from django.contrib import messages
from django.contrib.auth import logout, login, authenticate, get_user, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Max, Q, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST, require_http_methods
from django.views.csrf import csrf_failure as django_csrf_failure
from openpyxl import Workbook
from openpyxl.chart import BarChart, DoughnutChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.chart.series import DataPoint
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from carta.models import Plato
from carta.qr import generar_qr_mesa
from inventario.models import (
    GuarnicionRecetaItem,
    Insumo,
    MovimientoInventario,
    RecetaItem,
)
from pedidos.models import Factura, Mesa, Pedido, RechazoItemPedido
from .admin_layout import admin_page_context
from .models import Notificacion, ProductoIPVTurno, Turno, Usuario, cerrar_turno, configurar_turno, crear_mesas_del_turno, get_turno_abierto


ROLE_HOME = {
    'administrador': 'usuarios:dashboard',
    'cocina': 'pedidos:cocina',
    'mesera': 'pedidos:mesera',
    'cajera': 'pedidos:caja',
}
ROLES_CON_ACCESO_FINANCIERO = {'administrador', 'cajera'}


def _decimal_reporte(value):
    try:
        amount = Decimal(str(value).replace(',', '.'))
    except (InvalidOperation, TypeError, ValueError, AttributeError):
        return None
    return amount if amount.is_finite() else None


def _costos_recetas_reporte():
    costos_plato = defaultdict(lambda: Decimal('0'))
    platos_con_receta = set()
    for item in RecetaItem.objects.select_related('insumo').iterator(chunk_size=500):
        costos_plato[item.plato_id] += item.cantidad * item.insumo.costo
        platos_con_receta.add(item.plato_id)

    costos_guarnicion = defaultdict(lambda: Decimal('0'))
    guarniciones_con_receta = set()
    for item in GuarnicionRecetaItem.objects.select_related('insumo').iterator(chunk_size=500):
        costos_guarnicion[item.guarnicion_id] += item.cantidad * item.insumo.costo
        guarniciones_con_receta.add(item.guarnicion_id)

    return costos_plato, platos_con_receta, costos_guarnicion, guarniciones_con_receta


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


@never_cache
@login_required
def dashboard(request):
    hoy = timezone.localdate()
    puede_ver_datos_financieros = request.user.role in ROLES_CON_ACCESO_FINANCIERO
    turno = get_turno_abierto()
    turno_anterior = None
    turno_abierto_pendiente = None
    if request.user.role == 'administrador':
        turno = turno or Turno.objects.filter(fecha=hoy).prefetch_related('usuarios').order_by('-apertura').first()
        turno_anterior = Turno.objects.filter(
            estado='cerrado', fecha__lt=hoy,
        ).prefetch_related('usuarios').order_by('-fecha', '-apertura').first()
        turno_abierto_pendiente = Turno.objects.filter(
            estado='abierto', fecha__lt=hoy, continuidad_confirmada=False,
        ).order_by('-fecha', '-apertura').first()
    turno_requiere_confirmacion = bool(
        request.user.role == 'administrador'
        and turno
        and turno_abierto_pendiente
        and turno.pk == turno_abierto_pendiente.pk
    )
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
    notificaciones = list(
        Notificacion.objects.filter(destinatario=request.user)
        .select_related('turno')
        .prefetch_related('turno__usuarios')
        .order_by('-creada_en')[:10]
    )
    resumenes_notificacion = {}
    for notificacion in notificaciones:
        turno_notificacion = notificacion.turno
        if turno_notificacion and turno_notificacion.pk not in resumenes_notificacion:
            fin_turno_notificacion = turno_notificacion.cierre or timezone.now()
            resumenes_notificacion[turno_notificacion.pk] = turno_notificacion.facturas_queryset.filter(
                creado_en__lte=fin_turno_notificacion,
            ).aggregate(
                cantidad_facturas=Count('pk'),
                total_general_cup=Sum('total_cup'),
                total_efectivo_cup=Sum('monto_efectivo_cup'),
                total_transferencia_cup=Sum('monto_transferencia_cup'),
                total_usd=Sum('monto_usd'),
            )
        if turno_notificacion:
            notificacion.turno_resumen = resumenes_notificacion[turno_notificacion.pk]
    ipv_configurado = False
    opciones_ipv = []
    if turno and turno.estado == 'abierto' and request.user.role in ROLES_CON_ACCESO_FINANCIERO:
        _sincronizar_productos_ipv_turno(turno)
        ipv_configurado = ProductoIPVTurno.objects.filter(turno=turno).exists()
        opciones_ipv = [
            {
                'plato': registro.plato,
                'cantidad_inicial': registro.cantidad_inicial,
            }
            for registro in ProductoIPVTurno.objects.filter(turno=turno)
            .filter(plato__disponible=True)
            .select_related('plato__categoria')
            .order_by('plato__nombre')
        ]
    facturas_turno = Factura.objects.none()
    if turno and puede_ver_datos_financieros:
        fin_turno = turno.cierre or timezone.now()
        facturas_turno = Factura.objects.filter(
            creado_en__gte=turno.apertura,
            creado_en__lte=fin_turno,
        ).select_related('pedido__mesa').order_by('-creado_en')
    context = {
        'turno': turno,
        'hoy': hoy,
        'turno_anterior': turno_anterior,
        'resumen_turno_anterior': turno_anterior.resumen_financiero if turno_anterior and puede_ver_datos_financieros else None,
        'resumen_turno': turno.resumen_financiero if turno and puede_ver_datos_financieros else None,
        'puede_ver_datos_financieros': puede_ver_datos_financieros,
        'turno_abierto_pendiente': turno_abierto_pendiente,
        'turno_requiere_confirmacion': turno_requiere_confirmacion,
        'mesas_admin': mesas_admin,
        'mesas_activas_salon': mesas_activas_salon,
        'mesas_activas_exteriores': mesas_activas_exteriores,
        'facturas_turno': facturas_turno,
        'notificaciones': notificaciones,
        'ipv_configurado': ipv_configurado,
        'opciones_ipv': opciones_ipv,
    }
    if request.user.role == 'administrador':
        context.update(admin_page_context('dashboard'))
        return render(request, 'administrador/base_admin.html', context)
    return render(request, 'usuarios/dashboard.html', context)


@login_required
def gestion_usuarios_view(request):
    if request.user.role != 'administrador':
        messages.error(request, 'Solo el administrador puede gestionar usuarios.')
        return redirect('usuarios:dashboard')
    usuarios = list(Usuario.objects.order_by('role', 'username'))
    context = {
        'usuarios': usuarios,
        'usuarios_activos': sum(usuario.is_active for usuario in usuarios),
        'usuarios_inactivos': sum(not usuario.is_active for usuario in usuarios),
        'administradores': sum(usuario.role == 'administrador' for usuario in usuarios),
    }
    context.update(admin_page_context('users'))
    return render(request, 'administrador/base_admin.html', context)


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


@login_required
@require_POST
def continuar_turno_view(request):
    if request.user.role != 'administrador':
        messages.error(request, 'Solo el administrador puede confirmar la continuidad del turno.')
        return redirect('usuarios:dashboard')

    turno = get_object_or_404(
        Turno,
        pk=request.POST.get('turno_id'),
        estado='abierto',
        fecha__lt=timezone.localdate(),
    )
    turno.continuidad_confirmada = True
    turno.save(update_fields=['continuidad_confirmada'])
    messages.success(request, 'El turno continuará abierto hasta que se realice su cierre.')
    return redirect('usuarios:dashboard')


def _decimal_o_cero(valor):
    try:
        return Decimal(str(valor).replace(',', '.').strip())
    except (ArithmeticError, TypeError, ValueError, AttributeError):
        return Decimal('0')


def _decimal_ipv_no_negativo(valor, nombre_campo, decimal_places=3):
    try:
        cantidad = Decimal(str(valor).replace(',', '.').strip())
    except (ArithmeticError, TypeError, ValueError, AttributeError):
        raise ValueError(f'Introduce una cantidad válida para {nombre_campo}.')
    if not cantidad.is_finite() or cantidad < 0:
        raise ValueError(f'{nombre_campo.capitalize()} no puede ser negativo.')
    if cantidad.as_tuple().exponent < -decimal_places:
        raise ValueError(f'{nombre_campo.capitalize()} admite como máximo {decimal_places} decimales.')
    if cantidad and cantidad.adjusted() >= 12 - decimal_places:
        raise ValueError(f'{nombre_campo.capitalize()} supera el límite permitido.')
    return cantidad


def _ventas_facturadas_por_plato(turno, cierre, platos):
    platos_por_id = {plato.pk: plato for plato in platos}
    ids_por_nombre = {}
    for plato in platos:
        ids_por_nombre.setdefault(plato.nombre.strip().casefold(), []).append(plato.pk)

    ventas = {
        plato.pk: {
            'cantidad': Decimal('0'),
            'importe': Decimal('0'),
            'precio_total': Decimal('0'),
        }
        for plato in platos
    }
    snapshots = Factura.objects.filter(
        creado_en__gte=turno.apertura,
        creado_en__lte=cierre,
    ).values_list('items_snapshot', flat=True)
    for snapshot in snapshots:
        for item in snapshot or []:
            if item.get('es_merma'):
                continue
            plato_id = item.get('plato_id')
            try:
                plato_id = int(plato_id) if plato_id is not None else None
            except (TypeError, ValueError):
                plato_id = None
            if plato_id not in platos_por_id:
                nombre = str(item.get('plato', '')).strip().casefold()
                candidatos = ids_por_nombre.get(nombre, [])
                plato_id = candidatos[0] if len(candidatos) == 1 else None
            if plato_id is None:
                continue

            cantidad = _decimal_o_cero(item.get('cantidad'))
            datos = ventas[plato_id]
            datos['cantidad'] += cantidad
            datos['importe'] += _decimal_o_cero(item.get('subtotal'))
            datos['precio_total'] += _decimal_o_cero(item.get('precio_unit')) * cantidad
    return ventas


def _importe_ventas_facturadas(turno, cierre):
    snapshots = Factura.objects.filter(
        creado_en__gte=turno.apertura,
        creado_en__lte=cierre,
    ).values_list('items_snapshot', flat=True)
    return sum(
        (
            _decimal_o_cero(item.get('subtotal'))
            for snapshot in snapshots
            for item in snapshot or []
            if not item.get('es_merma')
        ),
        Decimal('0'),
    )


def _rechazos_por_plato(turno, cierre, platos=None):
    rechazos = RechazoItemPedido.objects.filter(
        creado_en__gte=turno.apertura,
        creado_en__lte=cierre,
    )
    if platos is not None:
        rechazos = rechazos.filter(item__plato_id__in=[plato.pk for plato in platos])
    cantidades = defaultdict(lambda: Decimal('0'))
    for plato_id, cantidad in rechazos.values_list('item__plato_id', 'cantidad'):
        cantidades[plato_id] += Decimal(cantidad)
    return cantidades


def _detalle_rechazos_ipv(turno, cierre):
    registros = RechazoItemPedido.objects.filter(
        creado_en__gte=turno.apertura,
        creado_en__lte=cierre,
    ).select_related('item__plato', 'item__pedido__mesa', 'reportado_por')
    return [{
        'plato_id': rechazo.item.plato_id,
        'plato': rechazo.item.plato.nombre,
        'cantidad': rechazo.cantidad,
        'motivo': rechazo.motivo,
        'reportado_por': (
            rechazo.reportado_por.get_full_name() or rechazo.reportado_por.username
        ) if rechazo.reportado_por else '',
        'creado_en': timezone.localtime(rechazo.creado_en).strftime('%d/%m/%Y %H:%M'),
        'mesa': rechazo.item.pedido.mesa.etiqueta if rechazo.item.pedido.mesa_id else 'Para llevar',
    } for rechazo in registros.order_by('creado_en', 'pk')]


def _cantidades_finales_productos_ipv(platos):
    if not platos:
        return {}
    registros = list(ProductoIPVTurno.objects.filter(
        plato_id__in=[plato.pk for plato in platos],
        turno__estado='cerrado',
        turno__cierre__isnull=False,
    ).select_related('turno', 'plato').order_by(
        'plato_id', '-turno__cierre', '-turno__apertura',
    ))
    ultimos_por_plato = {}
    for registro in registros:
        ultimos_por_plato.setdefault(registro.plato_id, registro)

    registros_por_turno = defaultdict(list)
    for registro in ultimos_por_plato.values():
        registros_por_turno[registro.turno_id].append(registro)

    cantidades = {}
    for registros_turno in registros_por_turno.values():
        turno_anterior = registros_turno[0].turno
        ventas = _ventas_facturadas_por_plato(
            turno_anterior,
            turno_anterior.cierre,
            [registro.plato for registro in registros_turno],
        )
        rechazos = _rechazos_por_plato(
            turno_anterior,
            turno_anterior.cierre,
            [registro.plato for registro in registros_turno],
        )
        for registro in registros_turno:
            cantidades[registro.plato_id] = (
                registro.cantidad_inicial
                + registro.entradas
                - registro.merma_rotura
                - ventas[registro.plato_id]['cantidad']
                - rechazos[registro.plato_id]
            )
    return cantidades


def _sincronizar_productos_ipv_turno(turno):
    platos = list(
        Plato.objects.filter(disponible=True)
        .select_related('categoria')
        .order_by('categoria__orden', 'nombre')
    )
    if not platos:
        return

    registros_existentes = set(
        ProductoIPVTurno.objects.filter(
            turno=turno,
            plato_id__in=[plato.pk for plato in platos],
        ).values_list('plato_id', flat=True)
    )
    platos_nuevos = [plato for plato in platos if plato.pk not in registros_existentes]
    cantidades_iniciales = _cantidades_finales_productos_ipv(platos_nuevos)
    ProductoIPVTurno.objects.bulk_create(
        [
            ProductoIPVTurno(
                turno=turno,
                plato=plato,
                cantidad_inicial=cantidades_iniciales.get(plato.pk, Decimal('0')),
            )
            for plato in platos_nuevos
        ],
        ignore_conflicts=True,
    )


@login_required
@require_POST
def configurar_ipv_turno_view(request):
    if request.user.role not in ROLES_CON_ACCESO_FINANCIERO:
        return JsonResponse({'error': 'Sin permiso'}, status=403)

    turno = get_turno_abierto()
    if turno is None:
        messages.error(request, 'No hay un turno abierto para configurar el IPV.')
        return redirect('usuarios:dashboard')

    try:
        fondo_inicial = _decimal_ipv_no_negativo(
            request.POST.get('fondo_inicial_cup'),
            'el fondo inicial',
            decimal_places=2,
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect('usuarios:dashboard')

    with transaction.atomic():
        turno = Turno.objects.select_for_update().filter(pk=turno.pk, estado='abierto').first()
        if turno is None:
            messages.error(request, 'El turno se cerró antes de configurar su IPV.')
            return redirect('usuarios:dashboard')
        turno.fondo_inicial_cup = fondo_inicial
        turno.save(update_fields=['fondo_inicial_cup'])
        _sincronizar_productos_ipv_turno(turno)

    messages.success(request, 'El fondo inicial quedó guardado y el IPV incluye automáticamente la carta disponible.')
    return redirect('usuarios:dashboard')


@login_required
@require_POST
def registrar_merma_ipv_view(request, producto_turno_id):
    if request.user.role not in ROLES_CON_ACCESO_FINANCIERO:
        return JsonResponse({'error': 'Sin permiso'}, status=403)
    try:
        cantidad = _decimal_ipv_no_negativo(request.POST.get('cantidad'), 'la rotura')
        if cantidad <= 0:
            raise ValueError('La cantidad de rotura debe ser mayor que cero.')
        with transaction.atomic():
            registro = ProductoIPVTurno.objects.select_for_update().select_related('turno').get(
                pk=producto_turno_id,
                turno__estado='abierto',
            )
            cierre = timezone.now()
            venta = _ventas_facturadas_por_plato(
                registro.turno,
                cierre,
                [registro.plato],
            )[registro.plato_id]
            disponible = (
                registro.cantidad_inicial
                + registro.entradas
                - registro.merma_rotura
                - venta['cantidad']
                - _rechazos_por_plato(
                    registro.turno,
                    cierre,
                    [registro.plato],
                )[registro.plato_id]
            )
            if cantidad > disponible:
                raise ValueError('La rotura no puede superar las unidades disponibles.')
            registro.merma_rotura += cantidad
            registro.save(update_fields=['merma_rotura'])
    except ProductoIPVTurno.DoesNotExist:
        return JsonResponse({'error': 'El producto ya no pertenece a un turno abierto.'}, status=404)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    return JsonResponse({'ok': True, 'merma_rotura': str(registro.merma_rotura)})


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
    datos = _datos_ipv_turno(turno)
    return JsonResponse({
        **datos,
        'turno_abierto': True,
        'productos': datos['filas'],
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
    if not activar and (
        mesa.abierta
        or Pedido.objects.filter(mesa=mesa).exclude(
            estado__in=['cerrado', 'rechazado', 'cancelado'],
        ).exists()
    ):
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
        if request.user.role == 'administrador':
            return redirect('usuarios:historial_turno_detalle', turno_id=turno.pk)
    except PermissionError as exc:
        messages.error(request, str(exc))
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect('usuarios:dashboard')


@login_required
def historial_turnos_view(request):
    if request.user.role not in ROLES_CON_ACCESO_FINANCIERO:
        messages.error(request, 'Solo caja o el administrador pueden ver el historial de turnos.')
        return redirect('usuarios:dashboard')

    turnos = Turno.objects.filter(estado='cerrado').prefetch_related('usuarios').order_by('-cierre', '-fecha')
    texto = request.GET.get('texto', '').strip()
    mes = request.GET.get('mes', '').strip()
    anio = request.GET.get('anio', '').strip()
    fecha_desde = request.GET.get('fecha_desde', '').strip()
    fecha_hasta = request.GET.get('fecha_hasta', '').strip()
    orden = request.GET.get('orden', 'reciente').strip()
    if orden not in {'reciente', 'antiguo', 'venta', 'rendimiento'}:
        orden = 'reciente'
    error_filtros = ''

    fecha_desde_valor = parse_date(fecha_desde) if fecha_desde else None
    fecha_hasta_valor = parse_date(fecha_hasta) if fecha_hasta else None
    if (fecha_desde and fecha_desde_valor is None) or (fecha_hasta and fecha_hasta_valor is None):
        error_filtros = 'Introduce un rango de fechas válido.'
    elif fecha_desde_valor and fecha_hasta_valor and fecha_desde_valor > fecha_hasta_valor:
        error_filtros = 'La fecha inicial debe ser anterior o igual a la fecha final.'

    if error_filtros:
        turnos = turnos.none()
    else:
        if fecha_desde_valor:
            turnos = turnos.filter(fecha__gte=fecha_desde_valor)
        if fecha_hasta_valor:
            turnos = turnos.filter(fecha__lte=fecha_hasta_valor)

        if mes:
            if mes.isdecimal() and 1 <= int(mes) <= 12:
                turnos = turnos.filter(fecha__month=int(mes))
            else:
                error_filtros = 'El mes seleccionado no es válido.'
                turnos = turnos.none()
        if anio:
            if anio.isdecimal() and len(anio) == 4:
                turnos = turnos.filter(fecha__year=int(anio))
            else:
                error_filtros = 'El año seleccionado no es válido.'
                turnos = turnos.none()

        if texto:
            busqueda = (
                Q(usuarios__username__icontains=texto)
                | Q(usuarios__first_name__icontains=texto)
                | Q(usuarios__last_name__icontains=texto)
                | Q(resumen__icontains=texto)
                | Q(observaciones__icontains=texto)
            )
            if texto.isdecimal():
                busqueda |= Q(pk=int(texto))
            turnos = turnos.filter(busqueda).distinct()

    historial = []
    total_general = Decimal('0')
    total_efectivo = Decimal('0')
    total_transferencia = Decimal('0')
    total_usd = Decimal('0')
    total_facturas = 0
    total_horas = Decimal('0')
    ventas_grafico = []

    for turno in turnos:
        data = turno.resumen_financiero
        historial.append({
            'turno': turno,
            'fondo_inicial_cup': turno.fondo_inicial_cup,
            'efectivo_esperado_cup': turno.fondo_inicial_cup + data['total_efectivo_cup'],
            **data,
        })
        ventas_grafico.append({
            'id': turno.pk,
            'fecha': turno.fecha.strftime('%d/%m/%Y'),
            'total': str(data['total_general_cup']),
            'url': reverse('usuarios:historial_turno_detalle', args=[turno.pk]),
        })
        total_general += data['total_general_cup']
        total_efectivo += data['total_efectivo_cup']
        total_transferencia += data['total_transferencia_cup']
        total_usd += data['total_usd']
        total_facturas += data['cantidad_facturas']
        total_horas += data['horas_abiertas']

    if orden == 'antiguo':
        historial.sort(key=lambda item: (item['turno'].apertura, item['turno'].pk))
    elif orden == 'venta':
        historial.sort(key=lambda item: (item['total_general_cup'], item['turno'].apertura), reverse=True)
    elif orden == 'rendimiento':
        historial.sort(key=lambda item: (item['rendimiento_por_hora_cup'], item['turno'].apertura), reverse=True)

    promedio_ipv = (total_general / Decimal(total_facturas)) if total_facturas else Decimal('0')
    rendimiento_promedio = (total_general / total_horas) if total_horas > 0 else Decimal('0')
    total_pagos_cup = total_efectivo + total_transferencia
    efectivo_porcentaje = (
        total_efectivo * Decimal('100') / total_pagos_cup
        if total_pagos_cup else Decimal('0')
    )
    transferencia_porcentaje = (
        total_transferencia * Decimal('100') / total_pagos_cup
        if total_pagos_cup else Decimal('0')
    )
    turnos_paginados = Paginator(historial, 10).get_page(request.GET.get('page'))
    pagination_query = request.GET.copy()
    pagination_query.pop('page', None)
    anios_disponibles = [
        fecha.year for fecha in Turno.objects.filter(estado='cerrado').dates('fecha', 'year', order='DESC')
    ]

    context = {
        'historial': turnos_paginados,
        'historial_pagination_query': pagination_query.urlencode(),
        'texto_filtro': texto,
        'mes_filtro': mes,
        'anio_filtro': anio,
        'fecha_desde_filtro': fecha_desde,
        'fecha_hasta_filtro': fecha_hasta,
        'orden_filtro': orden,
        'anios_disponibles': anios_disponibles,
        'error_filtros': error_filtros,
        'filtros_aplicados': any((texto, mes, anio, fecha_desde, fecha_hasta)),
        'ventas_grafico': ventas_grafico,
        'efectivo_porcentaje': efectivo_porcentaje,
        'transferencia_porcentaje': transferencia_porcentaje,
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
    if request.user.role == 'administrador':
        context.update(admin_page_context('history'))
        return render(request, 'administrador/base_admin.html', context)
    return render(request, 'usuarios/historial_turnos.html', context)


@login_required
def reportes_view(request):
    if request.user.role != 'administrador':
        messages.error(request, 'Solo el administrador puede consultar los reportes.')
        return redirect('usuarios:dashboard')

    hoy = timezone.localdate()
    mes_anterior_fecha = hoy.replace(day=1) - timedelta(days=1)
    inicio_semana = hoy - timedelta(days=hoy.weekday())
    fin_semana = inicio_semana + timedelta(days=6)
    periodo = request.GET.get('periodo', 'mes')
    periodo = {'mesant': 'mes_anterior', 'anio': 'ano'}.get(periodo, periodo)
    fecha_desde_raw = request.GET.get('fecha_desde', '').strip()
    fecha_hasta_raw = request.GET.get('fecha_hasta', '').strip()
    error_fechas = ''

    if periodo == 'hoy':
        fecha_desde = hoy
        fecha_hasta = hoy
        periodo_label = 'Hoy'
    elif periodo == 'mes':
        fecha_desde = hoy.replace(day=1)
        fecha_hasta = hoy
        periodo_label = 'Mes actual'
    elif periodo == 'mes_anterior':
        inicio_mes_actual = hoy.replace(day=1)
        fecha_hasta = inicio_mes_actual - timedelta(days=1)
        fecha_desde = fecha_hasta.replace(day=1)
        periodo_label = 'Mes anterior'
    elif periodo == 'ano':
        fecha_desde = hoy.replace(month=1, day=1)
        fecha_hasta = hoy
        periodo_label = 'Año actual'
    elif periodo == 'rango':
        try:
            fecha_desde = parse_date(fecha_desde_raw) if fecha_desde_raw else None
            fecha_hasta = parse_date(fecha_hasta_raw) if fecha_hasta_raw else None
        except ValueError:
            fecha_desde = None
            fecha_hasta = None
        periodo_label = 'Rango personalizado'
        if not fecha_desde or not fecha_hasta:
            error_fechas = 'Selecciona las fechas inicial y final para consultar el periodo.'
        elif fecha_desde > fecha_hasta:
            error_fechas = 'La fecha inicial debe ser anterior o igual a la fecha final.'
    else:
        periodo = 'semana'
        fecha_desde = hoy - timedelta(days=hoy.weekday())
        fecha_hasta = hoy
        periodo_label = 'Semana actual'

    agrupar_por_mes = (
        not error_fechas and (fecha_hasta - fecha_desde).days > 62
    )
    facturas = Factura.objects.none()
    if not error_fechas:
        desde = timezone.make_aware(datetime.combine(fecha_desde, datetime.min.time()))
        hasta = timezone.make_aware(datetime.combine(fecha_hasta, datetime.max.time()))
        facturas = Factura.objects.filter(creado_en__gte=desde, creado_en__lte=hasta).order_by('creado_en')

    platos = list(Plato.objects.select_related('categoria').order_by('nombre'))
    platos_por_id = {plato.pk: plato for plato in platos}
    platos_por_nombre = defaultdict(list)
    for plato in platos:
        platos_por_nombre[plato.nombre.strip().casefold()].append(plato)
    categorias_por_producto = {
        nombre.strip().casefold(): categoria or 'Sin categoría'
        for nombre, categoria in Plato.objects.values_list('nombre', 'categoria__nombre')
    }
    costos_plato, platos_con_receta, costos_guarnicion, guarniciones_con_receta = (
        _costos_recetas_reporte()
    )
    cantidades_productos = defaultdict(int)
    cantidades_categorias = defaultdict(int)
    unidades_por_plato = defaultdict(int)
    ventas_productos_costo = {}
    ventas_categorias_costo = {}
    unidades_sin_costo_receta = 0
    recaudacion_por_forma = {
        forma: {
            'forma': forma,
            'nombre': nombre,
            'facturas': 0,
            'total_cup': Decimal('0'),
            'total_usd': Decimal('0'),
        }
        for forma, nombre in Factura.FORMA_PAGO_CHOICES
    }
    recaudacion_por_periodo = defaultdict(lambda: Decimal('0'))
    facturas_por_periodo = defaultdict(int)
    recaudacion_por_hora = defaultdict(lambda: Decimal('0'))
    facturas_por_hora = defaultdict(int)
    total_cup = Decimal('0')
    total_usd = Decimal('0')
    total_efectivo_cup = Decimal('0')
    total_transferencia_cup = Decimal('0')
    total_unidades = 0
    items_invalidos = 0
    cantidad_facturas = 0
    reportes_export = []
    facturas_export = []
    inicio_periodo = fin_periodo = None
    dias_periodo = 1

    for factura in facturas.values(
        'id',
        'forma_pago',
        'total_cup',
        'monto_usd',
        'monto_efectivo_cup',
        'monto_transferencia_cup',
        'mesa_numero',
        'items_snapshot',
        'creado_en',
    ).iterator(chunk_size=500):
        cantidad_facturas += 1
        total_cup += factura['total_cup']
        total_usd += factura['monto_usd']
        total_efectivo_cup += factura['monto_efectivo_cup']
        total_transferencia_cup += factura['monto_transferencia_cup']

        forma_pago = factura['forma_pago']
        if forma_pago in recaudacion_por_forma:
            resumen_pago = recaudacion_por_forma[forma_pago]
            resumen_pago['facturas'] += 1
            resumen_pago['total_cup'] += factura['total_cup']
            resumen_pago['total_usd'] += factura['monto_usd']

        fecha_local = timezone.localtime(factura['creado_en'])
        fecha_factura = fecha_local.date()
        if fecha_desde == fecha_hasta:
            recaudacion_por_hora[fecha_local.hour] += factura['total_cup']
            facturas_por_hora[fecha_local.hour] += 1
        facturas_export.append([
            fecha_local.strftime('%d/%m/%Y'),
            factura['id'],
            fecha_local.strftime('%H:%M'),
            factura['mesa_numero'] or '',
            dict(Factura.FORMA_PAGO_CHOICES).get(forma_pago, forma_pago),
            str(factura['total_cup']),
            str(factura['monto_usd']),
        ])
        clave_periodo = fecha_factura.replace(day=1) if agrupar_por_mes else fecha_factura
        recaudacion_por_periodo[clave_periodo] += factura['total_cup']
        facturas_por_periodo[clave_periodo] += 1

        for item in factura['items_snapshot'] or []:
            if not isinstance(item, dict):
                items_invalidos += 1
                continue
            try:
                cantidad = int(item.get('cantidad', 1))
            except (TypeError, ValueError):
                items_invalidos += 1
                continue
            if cantidad <= 0:
                items_invalidos += 1
                continue
            nombre_producto = str(item.get('plato') or 'Producto sin nombre').strip()
            nombre_categoria = str(
                item.get('categoria')
                or categorias_por_producto.get(nombre_producto.casefold(), 'Sin categoría')
            ).strip()
            cantidades_productos[nombre_producto] += cantidad
            cantidades_categorias[nombre_categoria] += cantidad
            total_unidades += cantidad

            try:
                plato_id = int(item.get('plato_id'))
            except (TypeError, ValueError):
                plato_id = None
            plato = platos_por_id.get(plato_id)
            if plato is None:
                coincidencias = platos_por_nombre.get(nombre_producto.casefold(), [])
                plato = coincidencias[0] if len(coincidencias) == 1 else None
            if plato is not None:
                unidades_por_plato[plato.pk] += cantidad

            precio_unitario = _decimal_reporte(item.get('precio_unit'))
            if precio_unitario is None and plato is not None:
                precio_unitario = plato.precio
            costo_unitario = costos_plato.get(plato.pk) if plato and plato.pk in platos_con_receta else None
            guarnicion_id = item.get('guarnicion_id')
            if guarnicion_id not in (None, ''):
                try:
                    guarnicion_id = int(guarnicion_id)
                except (TypeError, ValueError):
                    guarnicion_id = None
                    costo_unitario = None
                if guarnicion_id is not None:
                    if guarnicion_id not in guarniciones_con_receta:
                        costo_unitario = None
                    elif costo_unitario is not None:
                        costo_unitario += costos_guarnicion[guarnicion_id]

            producto_costo = ventas_productos_costo.setdefault(
                (nombre_producto, nombre_categoria),
                {
                    'nombre': nombre_producto,
                    'categoria': nombre_categoria,
                    'unidades': 0,
                    'venta': Decimal('0'),
                    'unidades_con_costo': 0,
                    'costo': Decimal('0'),
                    'venta_total': Decimal('0'),
                    'precio_total': Decimal('0'),
                    'unidades_con_precio': 0,
                },
            )
            categoria_costo = ventas_categorias_costo.setdefault(
                nombre_categoria,
                {
                    'nombre': nombre_categoria,
                    'unidades': 0,
                    'venta': Decimal('0'),
                    'unidades_con_costo': 0,
                    'costo': Decimal('0'),
                },
            )
            producto_costo['unidades'] += cantidad
            categoria_costo['unidades'] += cantidad
            if precio_unitario is not None:
                producto_costo['venta_total'] += precio_unitario * cantidad
                producto_costo['precio_total'] += precio_unitario * cantidad
                producto_costo['unidades_con_precio'] += cantidad
            if costo_unitario is not None:
                producto_costo['unidades_con_costo'] += cantidad
                categoria_costo['unidades_con_costo'] += cantidad
                producto_costo['costo'] += costo_unitario * cantidad
                categoria_costo['costo'] += costo_unitario * cantidad
                if precio_unitario is not None:
                    importe = precio_unitario * cantidad
                    producto_costo['venta'] += importe
                    categoria_costo['venta'] += importe
            else:
                unidades_sin_costo_receta += cantidad

            reportes_export.append([
                factura['id'],
                fecha_factura.isoformat(),
                dict(Factura.FORMA_PAGO_CHOICES).get(forma_pago, forma_pago),
                str(factura['total_cup']),
                str(factura['monto_usd']),
                nombre_producto,
                nombre_categoria,
                cantidad,
            ])

    if not error_fechas:
        inicio_periodo = timezone.make_aware(
            datetime.combine(fecha_desde, datetime.min.time())
        )
        fin_periodo = timezone.make_aware(
            datetime.combine(fecha_hasta + timedelta(days=1), datetime.min.time())
        )
        dias_periodo = max((min(fecha_hasta, hoy) - fecha_desde).days + 1, 1)

    colores_grafico = ['#34d399', '#38bdf8', '#a78bfa', '#fbbf24', '#fb7185', '#2dd4bf', '#818cf8', '#f97316']
    colores_categoria = ['#34d399', '#117a60', '#b88d26', '#64748b', '#a3b8cc', '#475569']
    max_producto = max(cantidades_productos.values(), default=0)
    max_categoria = max(cantidades_categorias.values(), default=0)
    productos_mas_vendidos = [
        {
            'nombre': nombre,
            'categoria': categorias_por_producto.get(
                nombre.casefold(),
                'Sin categoría',
            ),
            'cantidad': cantidad,
            'porcentaje': max(2, round(cantidad * 100 / max_producto)),
        }
        for nombre, cantidad in sorted(
            cantidades_productos.items(),
            key=lambda fila: (-fila[1], fila[0].casefold()),
        )[:8]
    ] if max_producto else []
    for indice, producto in enumerate(productos_mas_vendidos):
        producto['color'] = colores_grafico[indice % len(colores_grafico)]
        producto['participacion'] = (
            round(producto['cantidad'] * 100 / total_unidades) if total_unidades else 0
        )

    ventas_por_categoria = [
        {
            'nombre': nombre,
            'cantidad': cantidad,
            'porcentaje': max(2, round(cantidad * 100 / max_categoria)),
        }
        for nombre, cantidad in sorted(
            cantidades_categorias.items(),
            key=lambda fila: (-fila[1], fila[0].casefold()),
        )
    ] if max_categoria else []

    for indice, categoria in enumerate(ventas_por_categoria):
        categoria['color'] = colores_categoria[indice % len(colores_categoria)]
        categoria['participacion'] = round(categoria['cantidad'] * 100 / total_unidades) if total_unidades else 0

    max_pago = max(
        (resumen['total_cup'] for resumen in recaudacion_por_forma.values()),
        default=Decimal('0'),
    )
    colores_pago = {
        'transferencia': '#2fb594',
        'efectivo_cup': '#f2994a',
        'mixto': '#8da2c0',
        'usd': '#e9c46a',
    }
    etiquetas_cortas_pago = {
        'transferencia': 'Transferencia',
        'efectivo_cup': 'Efectivo CUP',
        'mixto': 'Mixto',
        'usd': 'USD',
    }
    pagos = [
        {
            **resumen,
            'porcentaje': (
                max(2, round(resumen['total_cup'] * 100 / max_pago))
                if max_pago
                else 0
            ),
            'participacion': round(resumen['total_cup'] * 100 / total_cup) if total_cup else 0,
            'color': colores_pago[forma],
            'etiqueta_corta': etiquetas_cortas_pago[forma],
        }
        for forma, resumen in recaudacion_por_forma.items()
    ]

    pagos_con_recaudacion = [pago for pago in pagos if pago['total_cup'] > 0]
    segmentos_categorias = []
    acumulado_categorias = Decimal('0')
    for indice, categoria in enumerate(ventas_por_categoria):
        inicio = acumulado_categorias
        acumulado_categorias += Decimal(categoria['cantidad']) * Decimal('100') / Decimal(total_unidades)
        fin = Decimal('100') if indice == len(ventas_por_categoria) - 1 else acumulado_categorias
        segmentos_categorias.append(f"{categoria['color']} {inicio:.2f}% {fin:.2f}%")
    segmentos_pagos = []
    acumulado_pagos = Decimal('0')
    for indice, pago in enumerate(pagos_con_recaudacion):
        inicio = acumulado_pagos
        acumulado_pagos += pago['total_cup'] * Decimal('100') / total_cup
        fin = Decimal('100') if indice == len(pagos_con_recaudacion) - 1 else acumulado_pagos
        segmentos_pagos.append(f"{pago['color']} {inicio:.2f}% {fin:.2f}%")

    if not error_fechas:
        if agrupar_por_mes:
            fecha_periodo = fecha_desde.replace(day=1)
            while fecha_periodo <= fecha_hasta:
                recaudacion_por_periodo.setdefault(fecha_periodo, Decimal('0'))
                if fecha_periodo.month == 12:
                    fecha_periodo = fecha_periodo.replace(
                        year=fecha_periodo.year + 1,
                        month=1,
                    )
                else:
                    fecha_periodo = fecha_periodo.replace(
                        month=fecha_periodo.month + 1,
                    )
        else:
            fecha_periodo = fecha_desde
            while fecha_periodo <= fecha_hasta:
                recaudacion_por_periodo.setdefault(fecha_periodo, Decimal('0'))
                fecha_periodo += timedelta(days=1)

    recaudacion_periodo_anterior = defaultdict(lambda: Decimal('0'))
    periodos_anterior = []
    if not error_fechas:
        if agrupar_por_mes:
            periodos_actuales = sorted(recaudacion_por_periodo)
            primer_mes_actual = periodos_actuales[0]
            cursor = (primer_mes_actual - timedelta(days=1)).replace(day=1)
            for _ in periodos_actuales:
                periodos_anterior.append(cursor)
                cursor = (cursor - timedelta(days=1)).replace(day=1)
            periodos_anterior.reverse()
            inicio_anterior = periodos_anterior[0]
            fin_anterior_exclusivo = primer_mes_actual
        else:
            cantidad_dias = (fecha_hasta - fecha_desde).days + 1
            inicio_anterior = fecha_desde - timedelta(days=cantidad_dias)
            fin_anterior_exclusivo = fecha_desde
            cursor = inicio_anterior
            while cursor < fin_anterior_exclusivo:
                periodos_anterior.append(cursor)
                cursor += timedelta(days=1)

        inicio_anterior_aware = timezone.make_aware(
            datetime.combine(inicio_anterior, datetime.min.time())
        )
        fin_anterior_aware = timezone.make_aware(
            datetime.combine(fin_anterior_exclusivo, datetime.min.time())
        )
        facturas_anteriores = Factura.objects.filter(
            creado_en__gte=inicio_anterior_aware,
            creado_en__lt=fin_anterior_aware,
        ).values('creado_en', 'total_cup').iterator(chunk_size=500)
        for factura_anterior in facturas_anteriores:
            fecha_anterior = timezone.localtime(
                factura_anterior['creado_en']
            ).date()
            clave_anterior = (
                fecha_anterior.replace(day=1) if agrupar_por_mes else fecha_anterior
            )
            recaudacion_periodo_anterior[clave_anterior] += (
                factura_anterior['total_cup']
            )

    max_recaudacion = max(recaudacion_por_periodo.values(), default=Decimal('0'))
    meses_abreviados = (
        'Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun',
        'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic',
    )
    periodos_actuales = sorted(recaudacion_por_periodo.items())
    recaudacion_grafico = [
        {
            'etiqueta': (
                f'{meses_abreviados[fecha.month - 1]} {fecha.year}'
                if agrupar_por_mes
                else fecha.strftime('%d/%m')
            ),
            'total': total,
            'facturas': facturas_por_periodo.get(fecha, 0),
            'total_anterior': recaudacion_periodo_anterior.get(
                periodos_anterior[indice],
                Decimal('0'),
            ),
            'porcentaje': (
                max(2, round(total * 100 / max_recaudacion))
                if total and max_recaudacion
                else 0
            ),
            'es_maximo': bool(total and total == max_recaudacion),
        }
        for indice, (fecha, total) in enumerate(periodos_actuales)
    ] if not error_fechas else []

    if not error_fechas and fecha_desde == fecha_hasta:
        horas_con_ventas = [
            hora for hora, total in recaudacion_por_hora.items() if total > 0
        ]
        hora_inicio = max(0, min(horas_con_ventas) - 1) if horas_con_ventas else 11
        hora_fin = min(23, max(horas_con_ventas) + 1) if horas_con_ventas else 22
        recaudacion_grafico = [
            {
                'etiqueta': f'{hora:02d}h',
                'total': recaudacion_por_hora[hora],
                'facturas': facturas_por_hora[hora],
                'total_anterior': Decimal('0'),
                'porcentaje': 0,
                'es_maximo': False,
            }
            for hora in range(hora_inicio, hora_fin + 1)
        ]

    for indice, punto in enumerate(recaudacion_grafico):
        punto['color'] = colores_grafico[indice % len(colores_grafico)]

    reportes_costo_categorias = []
    for resumen in sorted(
        ventas_categorias_costo.values(),
        key=lambda item: (-item['unidades'], item['nombre'].casefold()),
    ):
        if not resumen['unidades_con_costo']:
            continue
        resumen['margen'] = resumen['venta'] - resumen['costo']
        resumen['margen_porcentaje'] = (
            resumen['margen'] * Decimal('100') / resumen['venta']
            if resumen['venta']
            else Decimal('0')
        )
        reportes_costo_categorias.append(resumen)

    reportes_costo_productos = []
    for resumen in ventas_productos_costo.values():
        resumen['precio_promedio'] = (
            resumen['precio_total'] / resumen['unidades_con_precio']
            if resumen['unidades_con_precio']
            else None
        )
        resumen['costo_unitario'] = (
            resumen['costo'] / resumen['unidades_con_costo']
            if resumen['unidades_con_costo']
            else None
        )
        resumen['margen'] = resumen['venta'] - resumen['costo']
        resumen['margen_porcentaje'] = (
            resumen['margen'] * Decimal('100') / resumen['venta']
            if resumen['venta']
            else Decimal('0')
        )
        reportes_costo_productos.append(resumen)
    reportes_costo_productos.sort(
        key=lambda item: (-item['margen'], item['nombre'].casefold())
    )
    venta_con_costo = sum(
        (item['venta'] for item in reportes_costo_categorias),
        Decimal('0'),
    )
    costo_ventas = sum(
        (item['costo'] for item in reportes_costo_categorias),
        Decimal('0'),
    )
    margen_bruto = venta_con_costo - costo_ventas
    margen_bruto_porcentaje = (
        margen_bruto * Decimal('100') / venta_con_costo
        if venta_con_costo
        else Decimal('0')
    )
    unidades_con_costo_total = sum(
        (item['unidades_con_costo'] for item in reportes_costo_categorias),
        0,
    )

    merma_filas = []
    merma_por_tipo = {}
    unidades_merma = Decimal('0')
    perdida_merma_conocida = Decimal('0')
    unidades_merma_sin_costo = Decimal('0')
    if inicio_periodo is not None:
        movimientos_merma = MovimientoInventario.objects.filter(
            tipo='merma',
            creado_en__gte=inicio_periodo,
            creado_en__lt=fin_periodo,
        ).select_related('insumo', 'usuario').order_by('-creado_en')
        for movimiento in movimientos_merma:
            cantidad = movimiento.cantidad
            costo = movimiento.insumo.costo if movimiento.insumo_id else None
            perdida = cantidad * costo if costo is not None else None
            tipo = 'Rotura'
            fila = {
                'fecha': timezone.localtime(movimiento.creado_en),
                'producto': movimiento.producto_nombre,
                'tipo': tipo,
                'cantidad': cantidad,
                'motivo': movimiento.motivo or 'Sin motivo registrado',
                'valor': perdida,
                'responsable': movimiento.responsable_nombre,
            }
            merma_filas.append(fila)
            resumen_tipo = merma_por_tipo.setdefault(
                tipo,
                {'tipo': tipo, 'cantidad': Decimal('0'), 'valor': Decimal('0')},
            )
            resumen_tipo['cantidad'] += cantidad
            unidades_merma += cantidad
            if perdida is None:
                unidades_merma_sin_costo += cantidad
            else:
                resumen_tipo['valor'] += perdida
                perdida_merma_conocida += perdida

        rechazos = RechazoItemPedido.objects.filter(
            creado_en__gte=inicio_periodo,
            creado_en__lt=fin_periodo,
        ).select_related(
            'item__plato',
            'item__guarnicion',
            'reportado_por',
        ).order_by('-creado_en')
        for rechazo in rechazos:
            plato = rechazo.item.plato
            costo_unitario = (
                costos_plato.get(plato.pk)
                if plato.pk in platos_con_receta
                else None
            )
            guarnicion = rechazo.item.guarnicion
            if guarnicion:
                if guarnicion.pk not in guarniciones_con_receta:
                    costo_unitario = None
                elif costo_unitario is not None:
                    costo_unitario += costos_guarnicion[guarnicion.pk]
            perdida = costo_unitario * rechazo.cantidad if costo_unitario is not None else None
            tipo = 'Rechazo'
            fila = {
                'fecha': timezone.localtime(rechazo.creado_en),
                'producto': plato.nombre,
                'tipo': tipo,
                'cantidad': rechazo.cantidad,
                'motivo': rechazo.motivo,
                'valor': perdida,
                'responsable': (
                    rechazo.reportado_por.get_full_name()
                    or rechazo.reportado_por.username
                    if rechazo.reportado_por
                    else 'Sin responsable'
                ),
            }
            merma_filas.append(fila)
            resumen_tipo = merma_por_tipo.setdefault(
                tipo,
                {'tipo': tipo, 'cantidad': Decimal('0'), 'valor': Decimal('0')},
            )
            resumen_tipo['cantidad'] += rechazo.cantidad
            unidades_merma += rechazo.cantidad
            if perdida is None:
                unidades_merma_sin_costo += rechazo.cantidad
            else:
                resumen_tipo['valor'] += perdida
                perdida_merma_conocida += perdida
    merma_por_tipo = sorted(
        merma_por_tipo.values(),
        key=lambda item: (-item['valor'], item['tipo']),
    )
    merma_filas.sort(key=lambda item: item['fecha'], reverse=True)

    rotacion_insumos = []
    if inicio_periodo is not None:
        consumo_por_insumo = {
            fila['insumo_id']: fila['cantidad'] or Decimal('0')
            for fila in MovimientoInventario.objects.filter(
                tipo='salida',
                creado_en__gte=inicio_periodo,
                creado_en__lt=fin_periodo,
                insumo_id__isnull=False,
            ).values('insumo_id').annotate(cantidad=Sum('cantidad'))
        }
        ultima_salida_por_insumo = {
            fila['insumo_id']: fila['ultima_salida']
            for fila in MovimientoInventario.objects.filter(
                tipo='salida',
                insumo_id__isnull=False,
            ).values('insumo_id').annotate(ultima_salida=Max('creado_en'))
        }
        for insumo in Insumo.objects.filter(activo=True).select_related(
            'categoria_inventario',
            'categoria',
        ).order_by('nombre'):
            consumo = consumo_por_insumo.get(insumo.pk, Decimal('0'))
            ultima_salida = ultima_salida_por_insumo.get(insumo.pk)
            dias_sin_salida = (
                (hoy - timezone.localtime(ultima_salida).date()).days
                if ultima_salida
                else None
            )
            cobertura = (
                insumo.stock_actual / (consumo / Decimal(dias_periodo))
                if consumo > 0
                else None
            )
            if insumo.stock_actual <= 0:
                estado, estado_clase = 'Agotado', 'bad'
            elif cobertura is not None:
                if cobertura <= 3:
                    estado, estado_clase = 'Rápida', 'ok'
                elif cobertura <= 10:
                    estado, estado_clase = 'Normal', 'info'
                elif cobertura <= 30:
                    estado, estado_clase = 'Lenta', 'warn'
                else:
                    estado, estado_clase = 'Muerta', 'bad'
            elif dias_sin_salida is not None and dias_sin_salida >= 15:
                estado, estado_clase = 'Muerta', 'bad'
            elif dias_sin_salida is not None and dias_sin_salida >= 7:
                estado, estado_clase = 'Lenta', 'warn'
            else:
                estado, estado_clase = 'Sin consumo', 'muted'
            rotacion_insumos.append({
                'nombre': insumo.nombre,
                'categoria': (
                    insumo.categoria_inventario.nombre
                    if insumo.categoria_inventario_id
                    else insumo.categoria.nombre
                    if insumo.categoria_id
                    else 'Sin categoría'
                ),
                'stock': insumo.stock_actual,
                'consumo': consumo,
                'rotacion': consumo / insumo.stock_actual if insumo.stock_actual > 0 else None,
                'cobertura': cobertura,
                'dias_sin_salida': dias_sin_salida,
                'valor_stock': insumo.stock_actual * insumo.costo,
                'estado': estado,
                'estado_clase': estado_clase,
                'ultima_salida': timezone.localtime(ultima_salida) if ultima_salida else None,
            })
    rotacion_insumos.sort(
        key=lambda item: (
            item['dias_sin_salida'] is None,
            -(item['dias_sin_salida'] or 0),
            item['nombre'].casefold(),
        )
    )

    meses_largos = (
        'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
        'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
    )
    dias_largos = (
        'lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo',
    )

    def fecha_larga(fecha, incluir_anio=True):
        resultado = f'{fecha.day} de {meses_largos[fecha.month - 1]}'
        if incluir_anio:
            resultado += f' de {fecha.year}'
        return resultado

    if fecha_desde and fecha_hasta and fecha_desde == fecha_hasta:
        periodo_subtitulo_prefijo = 'hoy, ' if periodo == 'hoy' else 'el '
        periodo_subtitulo_fecha = (
            f'{dias_largos[fecha_desde.weekday()]} {fecha_larga(fecha_desde)}'
        )
    elif periodo in ('mes', 'mes_anterior'):
        periodo_subtitulo_prefijo = ''
        periodo_subtitulo_fecha = (
            f'{meses_largos[fecha_desde.month - 1]} de {fecha_desde.year}'
        )
    elif periodo == 'ano':
        periodo_subtitulo_prefijo = 'el año '
        periodo_subtitulo_fecha = str(fecha_desde.year)
    elif fecha_desde and fecha_hasta:
        periodo_subtitulo_prefijo = (
            'la semana del ' if periodo == 'semana' else 'el período del '
        )
        inicio_subtitulo = (
            str(fecha_desde.day)
            if fecha_desde.year == fecha_hasta.year
            and fecha_desde.month == fecha_hasta.month
            else fecha_larga(
                fecha_desde,
                incluir_anio=fecha_desde.year != fecha_hasta.year,
            )
        )
        periodo_subtitulo_fecha = (
            f'{inicio_subtitulo} al {fecha_larga(fecha_hasta)}'
        )
    else:
        periodo_subtitulo_prefijo = ''
        periodo_subtitulo_fecha = periodo_label.lower()

    platos_huerfanos = []
    if not error_fechas:
        ultima_venta_por_plato = {}
        for fecha_factura, snapshot in Factura.objects.filter(
            creado_en__lte=timezone.now(),
        ).values_list('creado_en', 'items_snapshot').iterator(chunk_size=500):
            fecha_ultima_venta = timezone.localtime(fecha_factura).date()
            for item in snapshot or []:
                if not isinstance(item, dict):
                    continue
                try:
                    plato_id = int(item.get('plato_id'))
                except (TypeError, ValueError):
                    nombre = str(item.get('plato') or '').strip().casefold()
                    coincidencias = platos_por_nombre.get(nombre, [])
                    plato_id = coincidencias[0].pk if len(coincidencias) == 1 else None
                if plato_id in platos_por_id:
                    ultima_venta_por_plato[plato_id] = max(
                        fecha_ultima_venta,
                        ultima_venta_por_plato.get(plato_id, fecha_ultima_venta),
                    )

        fecha_corte = min(fecha_hasta, hoy)
        for plato in platos:
            if unidades_por_plato.get(plato.pk, 0):
                continue
            ultima_venta = ultima_venta_por_plato.get(plato.pk)
            dias_sin_venta = (
                (fecha_corte - ultima_venta).days if ultima_venta else None
            )
            if dias_sin_venta is None:
                recomendacion = 'Sin ventas históricas'
                recomendacion_clase = 'muted'
            elif dias_sin_venta >= 15:
                recomendacion = 'Retirar del menú'
                recomendacion_clase = 'bad'
            elif dias_sin_venta >= 7:
                recomendacion = 'Revisar / promocionar'
                recomendacion_clase = 'warn'
            else:
                recomendacion = 'Observar'
                recomendacion_clase = 'muted'
            platos_huerfanos.append({
                'nombre': plato.nombre,
                'categoria': plato.categoria.nombre if plato.categoria else 'Sin categoría',
                'precio': plato.precio,
                'costo': (
                    costos_plato.get(plato.pk)
                    if plato.pk in platos_con_receta
                    else None
                ),
                'dias_sin_venta': dias_sin_venta,
                'ultima_venta': ultima_venta,
                'recomendacion': recomendacion,
                'recomendacion_clase': recomendacion_clase,
                'disponible': plato.disponible,
            })
        platos_huerfanos.sort(
            key=lambda item: (
                item['dias_sin_venta'] is None,
                -(item['dias_sin_venta'] or 0),
                item['nombre'].casefold(),
            )
        )

    context = {
        'periodo': periodo,
        'periodo_label': periodo_label,
        'hoy': hoy,
        'mes_anterior_fecha': mes_anterior_fecha,
        'inicio_semana': inicio_semana,
        'fin_semana': fin_semana,
        'periodo_subtitulo_prefijo': periodo_subtitulo_prefijo,
        'periodo_subtitulo_fecha': periodo_subtitulo_fecha,
        'fecha_desde': fecha_desde,
        'fecha_hasta': fecha_hasta,
        'agrupar_por_mes': agrupar_por_mes,
        'fecha_desde_raw': fecha_desde_raw,
        'fecha_hasta_raw': fecha_hasta_raw,
        'error_fechas': error_fechas,
        'total_cup': total_cup,
        'total_usd': total_usd,
        'total_efectivo_cup': total_efectivo_cup,
        'total_transferencia_cup': total_transferencia_cup,
        'cantidad_facturas': cantidad_facturas,
        'promedio_factura': total_cup / cantidad_facturas if cantidad_facturas else Decimal('0'),
        'total_unidades': total_unidades,
        'items_invalidos': items_invalidos,
        'reportes_export': reportes_export,
        'facturas_export': facturas_export,
        'productos_unicos': len(cantidades_productos),
        'productos_mas_vendidos': productos_mas_vendidos,
        'reportes_costo_categorias': reportes_costo_categorias,
        'reportes_costo_productos': reportes_costo_productos,
        'venta_con_costo': venta_con_costo,
        'costo_ventas': costo_ventas,
        'margen_bruto': margen_bruto,
        'margen_bruto_porcentaje': margen_bruto_porcentaje,
        'unidades_con_costo_total': unidades_con_costo_total,
        'porcentaje_merma_sobre_costo': (
            perdida_merma_conocida * Decimal('100') / costo_ventas
            if costo_ventas
            else Decimal('0')
        ),
        'unidades_sin_costo_receta': unidades_sin_costo_receta,
        'unidades_merma': unidades_merma,
        'perdida_merma_conocida': perdida_merma_conocida,
        'unidades_merma_sin_costo': unidades_merma_sin_costo,
        'merma_por_tipo': merma_por_tipo,
        'merma_filas': merma_filas,
        'rotacion_insumos': rotacion_insumos,
        'platos_huerfanos': platos_huerfanos,
        'ventas_por_categoria': ventas_por_categoria,
        'pagos': pagos,
        'pagos_con_recaudacion': pagos_con_recaudacion,
        'categorias_donut': ', '.join(segmentos_categorias),
        'pagos_donut': ', '.join(segmentos_pagos),
        'recaudacion_grafico': recaudacion_grafico,
        'total_platos': len(platos),
        'cantidad_huerfanos_retirar': sum(
            plato['recomendacion'] == 'Retirar del menú'
            for plato in platos_huerfanos
        ),
        'total_stock_rotacion': sum(
            (insumo['stock'] for insumo in rotacion_insumos),
            Decimal('0'),
        ),
        'total_consumo_rotacion': sum(
            (insumo['consumo'] for insumo in rotacion_insumos),
            Decimal('0'),
        ),
        'valor_stock_rotacion': sum(
            (insumo['valor_stock'] for insumo in rotacion_insumos),
            Decimal('0'),
        ),
        'cantidad_rotacion_muerta': sum(
            insumo['estado_clase'] == 'bad' for insumo in rotacion_insumos
        ),
        'cantidad_rotacion_lenta': sum(
            insumo['estado_clase'] == 'warn' for insumo in rotacion_insumos
        ),
        'estados_rotacion': [
            {
                'nombre': 'Rápida',
                'clase': 'ok',
                'cantidad': sum(
                    insumo['estado_clase'] == 'ok' for insumo in rotacion_insumos
                ),
                'descripcion': '≤ 3 días',
            },
            {
                'nombre': 'Normal',
                'clase': 'info',
                'cantidad': sum(
                    insumo['estado_clase'] == 'info' for insumo in rotacion_insumos
                ),
                'descripcion': '≤ 10 días',
            },
            {
                'nombre': 'Lenta',
                'clase': 'warn',
                'cantidad': sum(
                    insumo['estado_clase'] == 'warn' for insumo in rotacion_insumos
                ),
                'descripcion': '≤ 30 días o sin ventas recientes',
            },
            {
                'nombre': 'Muerta / agotada',
                'clase': 'bad',
                'cantidad': sum(
                    insumo['estado_clase'] == 'bad' for insumo in rotacion_insumos
                ),
                'descripcion': '> 30 días o sin existencias',
            },
        ],
        'resumen_export': [
            ['Recaudación total', str(total_cup), 'CUP'],
            ['Recaudado en USD', str(total_usd), 'USD'],
            ['Facturas', cantidad_facturas, 'u.'],
            [
                'Promedio por factura',
                str(total_cup / cantidad_facturas if cantidad_facturas else Decimal('0')),
                'CUP',
            ],
            ['Unidades vendidas', total_unidades, 'u.'],
            ['Productos vendidos', len(cantidades_productos), 'SKU'],
            ['Margen bruto', str(margen_bruto), 'CUP'],
            ['Mermas (al costo)', str(perdida_merma_conocida), 'CUP'],
        ],
    }
    context.update(admin_page_context('reports'))
    return render(request, 'administrador/base_admin.html', context)


@login_required
def historial_turno_detalle_view(request, turno_id):
    if request.user.role not in ROLES_CON_ACCESO_FINANCIERO:
        messages.error(request, 'Solo caja o el administrador pueden ver el detalle del turno.')
        return redirect('usuarios:dashboard')

    turno = get_object_or_404(Turno.objects.prefetch_related('usuarios'), pk=turno_id)
    data = turno.resumen_financiero
    datos_ipv = _datos_ipv_turno(turno)
    data['fondo_inicial_cup'] = turno.fondo_inicial_cup
    data['efectivo_esperado_cup'] = Decimal(datos_ipv['efectivo_final_cup'])
    if turno.estado == 'abierto':
        duracion_segundos = max((timezone.now() - turno.apertura).total_seconds(), 0)
        data['horas_abiertas'] = Decimal(str(duracion_segundos / 3600))
        data['rendimiento_por_hora_cup'] = (
            data['total_general_cup'] / data['horas_abiertas']
            if data['horas_abiertas']
            else Decimal('0')
        )
    total_pagos_cup = data['total_efectivo_cup'] + data['total_transferencia_cup']
    porcentaje_efectivo = (
        int(data['total_efectivo_cup'] * 100 / total_pagos_cup + Decimal('0.5'))
        if total_pagos_cup
        else 0
    )
    porcentaje_transferencia = 100 - porcentaje_efectivo if total_pagos_cup else 0
    mesas_atendidas = turno.facturas_queryset.exclude(
        mesa_numero__isnull=True,
    ).values('mesa_numero').distinct().count()
    participantes = list(turno.usuarios.all())
    context = {
        'turno': turno,
        'detalles': data,
        'resumen': turno.resumen or 'Sin resumen registrado.',
        'mesas_atendidas': mesas_atendidas,
        'participantes': participantes,
        'porcentaje_efectivo': porcentaje_efectivo,
        'porcentaje_transferencia': porcentaje_transferencia,
        'duracion_supera_24h': data['horas_abiertas'] > Decimal('24'),
    }
    if request.user.role == 'administrador':
        context.update(admin_page_context(
            'turn_detail',
            admin_page_title=f'Detalle del turno #{turno.pk}',
            admin_badge_text=turno.get_estado_display(),
            admin_badge_icon='fa-circle-check' if turno.estado == 'cerrado' else 'fa-circle-dot',
        ))
        return render(request, 'administrador/base_admin.html', context)
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
    if turno.estado == 'abierto':
        _sincronizar_productos_ipv_turno(turno)
    cierre = turno.cierre if turno.estado == 'cerrado' and turno.cierre else timezone.now()
    registros_query = ProductoIPVTurno.objects.filter(turno=turno)
    if turno.estado == 'abierto':
        registros_query = registros_query.filter(plato__disponible=True)
    registros = list(registros_query.select_related('plato__categoria').order_by('plato__nombre'))
    ventas = _ventas_facturadas_por_plato(turno, cierre, [registro.plato for registro in registros])
    rechazos = _rechazos_por_plato(turno, cierre, [registro.plato for registro in registros])
    detalle_rechazos = _detalle_rechazos_ipv(turno, cierre)
    motivos_por_plato = defaultdict(list)
    for rechazo in detalle_rechazos:
        motivos_por_plato[rechazo['plato_id']].append(rechazo)
    facturas = Factura.objects.filter(creado_en__gte=turno.apertura, creado_en__lte=cierre)
    pagos = facturas.aggregate(
        efectivo=Sum('monto_efectivo_cup'),
        transferencia=Sum('monto_transferencia_cup'),
        usd=Sum('monto_usd'),
    )
    efectivo_ventas = pagos['efectivo'] or Decimal('0')
    fondo_inicial = turno.fondo_inicial_cup
    filas = []
    total_importe = _importe_ventas_facturadas(turno, cierre)
    for registro in registros:
        plato = registro.plato
        venta = ventas[plato.pk]
        cantidad_vendida = venta['cantidad']
        cantidad_rechazada = rechazos[plato.pk]
        importe_venta = venta['importe']
        saldo_final = (
            registro.cantidad_inicial
            + registro.entradas
            - cantidad_vendida
            - cantidad_rechazada
            - registro.merma_rotura
        )
        precio_venta = (
            importe_venta / cantidad_vendida
            if cantidad_vendida
            else plato.precio
        )
        filas.append({
            'id': registro.pk,
            'plato_id': plato.pk,
            'nombre': plato.nombre,
            'area': 'Carta',
            'categoria': plato.categoria.nombre if plato.categoria else 'Sin categoría',
            'unidad': 'Unidad',
            'saldo_inicial': str(registro.cantidad_inicial),
            'entradas': str(registro.entradas),
            'cantidad_existencia': str(saldo_final),
            'cantidad_vendida': str(cantidad_vendida),
            'cantidad_rechazada': str(cantidad_rechazada),
            'rechazos': motivos_por_plato[plato.pk],
            'saldo_final': str(saldo_final),
            'precio_venta': str(precio_venta),
            'importe_venta': str(importe_venta),
            'merma_rotura': str(registro.merma_rotura),
            'merma_url': reverse('usuarios:registrar_merma_ipv', args=[registro.pk]),
            'control_existencia': True,
        })

    return {
        'turno_id': turno.pk,
        'estado': turno.estado,
        'fecha': turno.fecha.strftime('%d/%m/%Y'),
        'actualizado_en': timezone.localtime(cierre).strftime('%H:%M:%S'),
        'total_importe': str(total_importe),
        'fondo_inicial_cup': f'{fondo_inicial:.2f}',
        'ventas_efectivo_cup': f'{efectivo_ventas:.2f}',
        'efectivo_final_cup': f'{fondo_inicial + efectivo_ventas:.2f}',
        'ventas_transferencia_cup': f'{pagos["transferencia"] or Decimal("0"):.2f}',
        'ventas_usd': f'{pagos["usd"] or Decimal("0"):.2f}',
        'filas': filas,
        'rechazos': detalle_rechazos,
    }


IPV_COLUMNAS = (
    ('Descripción', 'nombre'),
    ('Categoría', 'categoria'),
    ('Cant. vendida', 'cantidad_vendida'),
    ('Platos rechazados', 'cantidad_rechazada'),
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
    filas_tabla.append(
        [Paragraph('Total importe de ventas', estilo_celda)]
        + [''] * 4
        + [Paragraph(escape(datos['total_importe']), estilo_numero), '']
    )
    ancho_disponible = pagina[0] - 20 * mm
    proporciones = (0.24, 0.15, 0.10, 0.12, 0.12, 0.15, 0.12)
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
        ('SPAN', (0, -1), (4, -1)),
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
        Paragraph(
            f"Fondo inicial: {escape(datos['fondo_inicial_cup'])} CUP · "
            f"Efectivo vendido: {escape(datos['ventas_efectivo_cup'])} CUP · "
            f"Efectivo esperado: {escape(datos['efectivo_final_cup'])} CUP · "
            f"Transferencias: {escape(datos['ventas_transferencia_cup'])} CUP · "
            f"USD: {escape(datos['ventas_usd'])}",
            estilos['Normal'],
        ),
        Spacer(1, 8 * mm),
        tabla,
    ]
    if datos['rechazos']:
        estilo_detalle = ParagraphStyle(
            'IPVDetalleRechazo',
            parent=estilo_celda,
            fontSize=7,
            leading=8,
        )
        encabezados_rechazo = ['Fecha', 'Mesa', 'Plato', 'Cantidad', 'Motivo', 'Reportado por']
        detalle = [[Paragraph(escape(valor), estilo_encabezado) for valor in encabezados_rechazo]]
        for rechazo in datos['rechazos']:
            detalle.append([
                Paragraph(escape(rechazo['creado_en']), estilo_detalle),
                Paragraph(escape(rechazo['mesa']), estilo_detalle),
                Paragraph(escape(rechazo['plato']), estilo_detalle),
                Paragraph(str(rechazo['cantidad']), estilo_detalle),
                Paragraph(escape(rechazo['motivo']), estilo_detalle),
                Paragraph(escape(rechazo['reportado_por']), estilo_detalle),
            ])
        tabla_rechazos = Table(detalle, colWidths=[
            ancho_disponible * 0.15,
            ancho_disponible * 0.16,
            ancho_disponible * 0.18,
            ancho_disponible * 0.09,
            ancho_disponible * 0.27,
            ancho_disponible * 0.15,
        ], repeatRows=1)
        tabla_rechazos.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e293b')),
            ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#cbd5e1')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        contenido.extend([
            Spacer(1, 8 * mm),
            Paragraph('Detalle de platos rechazados', estilos['Heading2']),
            tabla_rechazos,
        ])
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
    libro.remove(libro.active)
    libro.calculation.fullCalcOnLoad = True
    libro.calculation.forceFullCalc = True

    verde = 'FF1E5631'
    verde_medio = 'FF2E7D45'
    verde_claro = 'FFE8F3EA'
    total_fill = 'FFCFE8D5'
    borde_color = 'FFD0D7D2'
    texto_color = 'FF1F2933'
    gris_color = 'FF6B7280'
    formato_cup = '#,##0.00" CUP"'
    borde_fino = Side(style='thin', color=borde_color)
    bordes = Border(left=borde_fino, right=borde_fino, top=borde_fino, bottom=borde_fino)

    def escribir(celda, valor, *, negrita=False, color=texto_color, fondo=None,
                 alineacion='center', envolver=False, formato=None, sangria=0,
                 borde=True, tamano=10, formula=False):
        celda.value = valor
        if isinstance(valor, str):
            celda.data_type = 'f' if formula else 's'
        celda.font = Font(name='Calibri', size=tamano, bold=negrita, color=color)
        if fondo:
            celda.fill = PatternFill('solid', fgColor=fondo)
        celda.alignment = Alignment(
            horizontal=alineacion,
            vertical='center',
            wrap_text=envolver,
            indent=sangria,
        )
        if formato:
            celda.number_format = formato
        if borde:
            celda.border = bordes
        return celda

    def banda(hoja, fila, desde, hasta, titulo, *, fondo=verde, color='FFFFFFFF', tamano=12):
        hoja.merge_cells(start_row=fila, start_column=desde, end_row=fila, end_column=hasta)
        for columna in range(desde, hasta + 1):
            hoja.cell(fila, columna).fill = PatternFill('solid', fgColor=fondo)
        escribir(
            hoja.cell(fila, desde), titulo, negrita=True, color=color,
            fondo=fondo, borde=False, tamano=tamano,
        )

    def encabezado(hoja, fila, titulos):
        borde_encabezado = Border(
            left=Side(style='thin', color='FF3B7A52'),
            right=Side(style='thin', color='FF3B7A52'),
            top=Side(style='thin', color=verde),
            bottom=Side(style='medium', color=verde_medio),
        )
        for columna, titulo in enumerate(titulos, start=1):
            escribir(
                hoja.cell(fila, columna), titulo, negrita=True,
                color='FFFFFFFF', fondo=verde, envolver=True,
            ).border = borde_encabezado
        hoja.row_dimensions[fila].height = 26

    def preparar_hoja(hoja, anchos, *, congelar='A5', color=verde_medio):
        hoja.sheet_properties.tabColor = color
        hoja.sheet_view.showGridLines = False
        hoja.freeze_panes = congelar
        hoja.page_setup.orientation = 'landscape'
        hoja.page_setup.fitToWidth = 1
        hoja.page_setup.fitToHeight = 0
        hoja.sheet_properties.pageSetUpPr.fitToPage = True
        hoja.page_margins.left = .4
        hoja.page_margins.right = .4
        hoja.page_margins.top = .5
        hoja.page_margins.bottom = .5
        for columna, ancho in enumerate(anchos, start=1):
            hoja.column_dimensions[get_column_letter(columna)].width = ancho

    def metadato(hoja, fila, desde, hasta, valor):
        hoja.merge_cells(start_row=fila, start_column=desde, end_row=fila, end_column=hasta)
        for columna in range(desde, hasta + 1):
            hoja.cell(fila, columna).fill = PatternFill('solid', fgColor=verde_claro)
        escribir(
            hoja.cell(fila, desde), valor, negrita=True, color=verde,
            fondo=verde_claro, alineacion='left', sangria=1, borde=False,
        )

    def kpi(hoja, fila, columna, etiqueta, valor, pie, color, formato=None):
        hasta = columna + 2
        for desplazamiento, contenido in enumerate((etiqueta, valor, pie)):
            actual = fila + desplazamiento
            hoja.merge_cells(start_row=actual, start_column=columna, end_row=actual, end_column=hasta)
            for col in range(columna, hasta + 1):
                hoja.cell(actual, col).fill = PatternFill('solid', fgColor='FFFFFFFF')
            escribir(
                hoja.cell(actual, columna), contenido,
                negrita=desplazamiento < 2,
                tamano=(9, 18, 9)[desplazamiento],
                color=color if desplazamiento < 2 else gris_color,
                fondo='FFFFFFFF',
                alineacion='center',
                formato=formato if desplazamiento == 1 else None,
            )
            for col in range(columna, hasta + 1):
                hoja.cell(actual, col).border = Border(
                    left=borde_fino if col == columna else Side(),
                    right=borde_fino if col == hasta else Side(),
                    top=borde_fino if desplazamiento == 0 else Side(),
                    bottom=borde_fino if desplazamiento == 2 else Side(),
                )

    filas_ipv = datos['filas']
    facturas = list(Factura.objects.filter(
        creado_en__gte=turno.apertura,
        creado_en__lte=turno.cierre or timezone.now(),
    ).select_related('pedido__mesa').order_by('creado_en', 'pk'))
    filas_facturas = []
    for factura in facturas:
        moneda = 'USD' if factura.monto_usd > 0 else 'CUP'
        filas_facturas.append({
            'numero': factura.pk,
            'hora': timezone.localtime(factura.creado_en).strftime('%H:%M'),
            'mesa': factura.pedido.mesa.etiqueta if factura.pedido.mesa_id else 'Para llevar',
            'pago': factura.get_forma_pago_display(),
            'moneda': moneda,
            'total': factura.monto_usd if moneda == 'USD' else factura.total_cup,
        })

    total_cup = sum(
        (factura.total_cup for factura in facturas),
        Decimal('0'),
    )
    efectivo = Decimal(datos['ventas_efectivo_cup'])
    transferencia = Decimal(datos['ventas_transferencia_cup'])
    fondo = Decimal(datos['fondo_inicial_cup'])
    ventas_usd = Decimal(datos['ventas_usd'])
    total_roturas = sum((Decimal(fila['merma_rotura']) for fila in filas_ipv), Decimal('0'))
    total_rechazados = sum((Decimal(rechazo['cantidad']) for rechazo in datos['rechazos']), Decimal('0'))

    categorias = {}
    for fila in filas_ipv:
        cantidad = Decimal(fila['cantidad_vendida'])
        if cantidad <= 0:
            continue
        nombre = fila['categoria']
        categoria = categorias.setdefault(nombre, {'unidades': Decimal('0'), 'importe': Decimal('0')})
        categoria['unidades'] += cantidad
        categoria['importe'] += Decimal(fila['importe_venta'])
    categorias_ordenadas = sorted(
        categorias.items(), key=lambda item: (-item[1]['importe'], item[0].casefold()),
    )
    pagos = {}
    for factura in facturas:
        if factura.monto_usd > 0:
            continue
        pago = factura.get_forma_pago_display()
        resumen_pago = pagos.setdefault(pago, {'facturas': 0, 'total': Decimal('0')})
        resumen_pago['facturas'] += 1
        resumen_pago['total'] += factura.total_cup
    pagos_ordenados = sorted(pagos.items(), key=lambda item: (-item[1]['total'], item[0]))

    resumen = libro.create_sheet('Resumen')
    preparar_hoja(resumen, [12, 12, 12, 2, 12, 12, 12, 2, 12, 12, 12, 2, 12, 12, 12], color=verde)
    resumen.row_dimensions[1].height = 8
    banda(resumen, 2, 1, 15, 'RESUMEN DEL TURNO · GOURMETCONTROL', tamano=16)
    resumen.row_dimensions[2].height = 34
    cierre_texto = (
        timezone.localtime(turno.cierre).strftime('%d/%m/%Y %H:%M')
        if turno.cierre else 'Pendiente'
    )
    banda(
        resumen, 3, 1, 15,
        f'Fecha: {turno.fecha:%d/%m/%Y}   ·   Apertura: '
        f'{timezone.localtime(turno.apertura):%d/%m/%Y %H:%M}   ·   '
        f'Cierre: {cierre_texto}   ·   Estado: {turno.get_estado_display()}',
        fondo=verde_claro, color=verde, tamano=10,
    )
    resumen.row_dimensions[3].height = 22
    resumen.row_dimensions[4].height = 10
    kpis = (
        ('VENTAS TOTALES', total_cup, 'Ingresos registrados', verde, formato_cup),
        ('FACTURAS', len(facturas), 'Órdenes procesadas', 'FF1F3A8A', '0'),
        ('EFECTIVO', efectivo, 'Caja física', 'FFB7791F', formato_cup),
        ('TRANSFERENCIA', transferencia, 'Pagos digitales', 'FF6B21A8', formato_cup),
        ('FONDO INICIAL', fondo, 'Caja al abrir el turno', verde, formato_cup),
        ('EFECTIVO ESPERADO', fondo + efectivo, 'Fondo + ventas en efectivo', 'FF1F3A8A', formato_cup),
        ('VENTAS USD', ventas_usd, 'Facturas en dólares', 'FFB7791F', '#,##0.00" USD"'),
        ('ROTURAS / RECHAZOS', f'{total_roturas} / {total_rechazados}', 'Roturas y platos rechazados', 'FF9C1C1C', '@'),
    )
    for fila, grupo in ((5, kpis[:4]), (9, kpis[4:])):
        for indice, valores in enumerate(grupo):
            columna = 1 + indice * 4
            kpi(resumen, fila, columna, *valores)
            resumen.row_dimensions[fila].height = 20
            resumen.row_dimensions[fila + 1].height = 34
            resumen.row_dimensions[fila + 2].height = 18
    resumen.row_dimensions[8].height = 10
    resumen.row_dimensions[12].height = 10
    banda(resumen, 13, 1, 7, 'VENTAS POR CATEGORÍA')
    banda(resumen, 13, 9, 15, 'VENTAS POR FORMA DE PAGO (CUP)')
    resumen.row_dimensions[13].height = 22

    for fila in range(14, 29):
        resumen.row_dimensions[fila].height = 15
    cantidad_categorias = max(len(categorias_ordenadas), 1)
    cantidad_pagos = max(len(pagos_ordenados), 1)
    chart_barras = BarChart()
    chart_barras.type = 'col'
    chart_barras.style = 10
    chart_barras.title = 'Importe de ventas por categoría (CUP)'
    chart_barras.y_axis.title = 'CUP'
    chart_barras.height = 7.5
    chart_barras.width = 15
    chart_barras.legend = None
    chart_barras.add_data(
        Reference(resumen, min_col=6, min_row=30, max_row=30 + cantidad_categorias),
        titles_from_data=True,
    )
    chart_barras.set_categories(
        Reference(resumen, min_col=1, min_row=31, max_row=30 + cantidad_categorias),
    )
    colores_graficos = ('FF2E8B57', 'FFF2B01E', 'FFC0392B', 'FF1F4E9C', 'FF7B3FA0', 'FF16A2A8')
    if chart_barras.series:
        chart_barras.series[0].data_points = [
            DataPoint(
                idx=indice,
                spPr=GraphicalProperties(solidFill=colores_graficos[indice % len(colores_graficos)]),
            )
            for indice in range(cantidad_categorias)
        ]
    resumen.add_chart(chart_barras, 'A14')

    chart_pagos = DoughnutChart()
    chart_pagos.style = 10
    chart_pagos.title = 'Ventas por forma de pago'
    chart_pagos.holeSize = 55
    chart_pagos.height = 7.5
    chart_pagos.width = 15
    chart_pagos.varyColors = True
    chart_pagos.add_data(
        Reference(resumen, min_col=14, min_row=30, max_row=30 + cantidad_pagos),
        titles_from_data=True,
    )
    chart_pagos.set_categories(
        Reference(resumen, min_col=9, min_row=31, max_row=30 + cantidad_pagos),
    )
    if chart_pagos.series:
        chart_pagos.series[0].data_points = [
            DataPoint(
                idx=indice,
                spPr=GraphicalProperties(solidFill=colores_graficos[indice % len(colores_graficos)]),
            )
            for indice in range(cantidad_pagos)
        ]
        chart_pagos.dataLabels = DataLabelList(showCatName=True, showVal=True, showPercent=True)
    resumen.add_chart(chart_pagos, 'I14')

    for desde, hasta, titulo in ((1, 3, 'Categoría'), (4, 5, 'Unidades'), (6, 7, 'Importe'),
                                 (9, 11, 'Forma de pago'), (12, 13, 'Facturas'), (14, 15, 'Total')):
        resumen.merge_cells(start_row=30, start_column=desde, end_row=30, end_column=hasta)
        for columna in range(desde, hasta + 1):
            resumen.cell(30, columna).fill = PatternFill('solid', fgColor=verde)
            resumen.cell(30, columna).border = bordes
        escribir(
            resumen.cell(30, desde), titulo, negrita=True, color='FFFFFFFF',
            fondo=verde, alineacion='left' if desde in (1, 9) else 'center',
        )
    filas_resumen = max(len(categorias_ordenadas), len(pagos_ordenados), 1)
    for indice in range(filas_resumen):
        fila = 31 + indice
        categoria = categorias_ordenadas[indice] if indice < len(categorias_ordenadas) else None
        pago = pagos_ordenados[indice] if indice < len(pagos_ordenados) else None
        valores = (
            (1, 3, categoria[0] if categoria else '', 'left', None),
            (4, 5, categoria[1]['unidades'] if categoria else '', 'right', '#,##0.###'),
            (6, 7, categoria[1]['importe'] if categoria else '', 'right', formato_cup),
            (9, 11, pago[0] if pago else ('Sin facturas' if indice == 0 else ''), 'left', None),
            (12, 13, pago[1]['facturas'] if pago else '', 'right', '0'),
            (14, 15, pago[1]['total'] if pago else '', 'right', formato_cup),
        )
        for desde, hasta, valor, alineacion, formato in valores:
            resumen.merge_cells(start_row=fila, start_column=desde, end_row=fila, end_column=hasta)
            for columna in range(desde, hasta + 1):
                resumen.cell(fila, columna).border = bordes
            escribir(
                resumen.cell(fila, desde), valor, alineacion=alineacion,
                sangria=1 if alineacion == 'left' else 0, formato=formato,
            )
    fila_total = 31 + filas_resumen
    for desde, hasta, valor, formato, alineacion in (
        (1, 3, 'TOTAL', None, 'left'),
        (4, 5, f'=SUM(D31:D{fila_total - 1})', '#,##0.###', 'right'),
        (6, 7, f'=SUM(F31:F{fila_total - 1})', formato_cup, 'right'),
        (9, 11, 'TOTAL', None, 'left'),
        (12, 13, f'=SUM(L31:L{fila_total - 1})', '0', 'right'),
        (14, 15, f'=SUM(N31:N{fila_total - 1})', formato_cup, 'right'),
    ):
        resumen.merge_cells(start_row=fila_total, start_column=desde, end_row=fila_total, end_column=hasta)
        for columna in range(desde, hasta + 1):
            resumen.cell(fila_total, columna).fill = PatternFill('solid', fgColor=total_fill)
            resumen.cell(fila_total, columna).border = bordes
        escribir(
            resumen.cell(fila_total, desde), valor, negrita=True, color=verde,
            fondo=total_fill, alineacion=alineacion, formato=formato,
            sangria=1 if alineacion == 'left' else 0, formula=valor.startswith('='),
        )
    if turno.observaciones:
        resumen.row_dimensions[fila_total + 1].height = 10
        banda(resumen, fila_total + 2, 1, 15, 'OBSERVACIONES DEL CIERRE')
        resumen.merge_cells(start_row=fila_total + 3, start_column=1, end_row=fila_total + 3, end_column=15)
        escribir(
            resumen.cell(fila_total + 3, 1), turno.observaciones,
            alineacion='left', envolver=True, sangria=1,
        )
        resumen.row_dimensions[fila_total + 3].height = 40

    vendidos = [
        fila for fila in filas_ipv
        if Decimal(fila['cantidad_vendida']) > 0
    ]
    vendidos.sort(
        key=lambda fila: (-Decimal(fila['importe_venta']), fila['nombre'].casefold()),
    )
    productos = libro.create_sheet('Productos vendidos')
    preparar_hoja(productos, [7, 34, 20, 16, 16, 18, 14], color='FFB7791F')
    banda(productos, 1, 1, 7, 'PRODUCTOS VENDIDOS DEL DÍA', tamano=16)
    productos.row_dimensions[1].height = 34
    productos.merge_cells('A2:G2')
    escribir(
        productos['A2'],
        f'Fecha: {turno.fecha:%d/%m/%Y}   ·   {len(vendidos)} productos vendidos   ·   '
        f'{sum((Decimal(fila["cantidad_vendida"]) for fila in vendidos), Decimal("0"))} unidades',
        negrita=True, color=verde, fondo=verde_claro, alineacion='left', sangria=1, borde=False,
    )
    productos.row_dimensions[2].height = 22
    productos.row_dimensions[3].height = 8
    encabezado(productos, 4, ['N.º', 'Producto', 'Categoría', 'Cantidad vendida', 'Precio venta', 'Importe ventas', '% del total'])
    total_vendidos = sum((Decimal(fila['importe_venta']) for fila in vendidos), Decimal('0'))
    fila_total_vendidos = 5 + len(vendidos)
    for indice, fila in enumerate(vendidos, start=1):
        actual = 4 + indice
        escribir(productos.cell(actual, 1), indice, color=gris_color, formato='0')
        escribir(productos.cell(actual, 2), fila['nombre'], negrita=True, alineacion='left', sangria=1)
        escribir(productos.cell(actual, 3), fila['categoria'], color=gris_color, alineacion='left', sangria=1)
        escribir(productos.cell(actual, 4), Decimal(fila['cantidad_vendida']), negrita=True, formato='#,##0.###')
        escribir(productos.cell(actual, 5), Decimal(fila['precio_venta']), alineacion='right', formato=formato_cup)
        escribir(
            productos.cell(actual, 6), f'=D{actual}*E{actual}',
            negrita=True, color=verde, alineacion='right', formato=formato_cup, formula=True,
        )
        escribir(
            productos.cell(actual, 7), f'=F{actual}/F${fila_total_vendidos}',
            formato='0.0%', formula=True,
        )
        productos.row_dimensions[actual].height = 20
    if not vendidos:
        productos.merge_cells('A5:G5')
        escribir(productos['A5'], 'Aún no hay productos vendidos en este turno.', color=gris_color)
        productos.row_dimensions[5].height = 22
    else:
        for columna in range(1, 8):
            productos.cell(fila_total_vendidos, columna).fill = PatternFill('solid', fgColor=total_fill)
            productos.cell(fila_total_vendidos, columna).border = bordes
        escribir(productos.cell(fila_total_vendidos, 2), 'TOTAL', negrita=True, color=verde, fondo=total_fill, alineacion='left', sangria=1)
        escribir(productos.cell(fila_total_vendidos, 4), f'=SUM(D5:D{fila_total_vendidos - 1})', negrita=True, color=verde, fondo=total_fill, formato='#,##0.###', formula=True)
        escribir(productos.cell(fila_total_vendidos, 6), f'=SUM(F5:F{fila_total_vendidos - 1})', negrita=True, color=verde, fondo=total_fill, alineacion='right', formato=formato_cup, formula=True)
        escribir(productos.cell(fila_total_vendidos, 7), f'=SUM(G5:G{fila_total_vendidos - 1})', negrita=True, color=verde, fondo=total_fill, formato='0.0%', formula=True)
        productos.row_dimensions[fila_total_vendidos].height = 24
        productos.auto_filter.ref = f'A4:G{fila_total_vendidos - 1}'

    ipv = libro.create_sheet('IPV turno actual')
    preparar_hoja(ipv, [34, 20, 12, 16, 18, 12, 16])
    banda(ipv, 1, 1, 7, 'INVENTARIO PERPETUO DE VENTAS', tamano=16)
    ipv.row_dimensions[1].height = 34
    metadato(ipv, 2, 1, 2, 'RESTAURANT: ________')
    metadato(ipv, 2, 3, 4, f'FECHA: {turno.fecha:%d/%m/%Y}')
    metadato(ipv, 2, 5, 7, f'TOTAL: {Decimal(datos["total_importe"]):,.2f} CUP')
    ipv.row_dimensions[2].height = 22
    ipv.row_dimensions[3].height = 8
    encabezado(ipv, 4, ['Producto', 'Categoría', 'Vendido', 'Precio venta', 'Importe ventas', 'Roturas', 'Estado'])
    for indice, fila in enumerate(filas_ipv):
        actual = 5 + indice
        vendido = Decimal(fila['cantidad_vendida'])
        roturas = Decimal(fila['merma_rotura'])
        if roturas > 0:
            estado, fondo, color = 'Con roturas', 'FFF8D0D0', 'FF9C1C1C'
        elif vendido > 0:
            estado, fondo, color = 'Con ventas', total_fill, 'FF1E6B34'
        else:
            estado, fondo, color = 'Sin ventas', 'FFEDEFF1', gris_color
        escribir(ipv.cell(actual, 1), fila['nombre'], negrita=vendido > 0, alineacion='left', sangria=1)
        escribir(ipv.cell(actual, 2), fila['categoria'], color=gris_color, alineacion='left', sangria=1)
        escribir(ipv.cell(actual, 3), vendido, negrita=vendido > 0, formato='#,##0.###')
        escribir(ipv.cell(actual, 4), Decimal(fila['precio_venta']), alineacion='right', formato=formato_cup)
        escribir(
            ipv.cell(actual, 5), f'=C{actual}*D{actual}',
            negrita=vendido > 0, color=verde if vendido > 0 else texto_color,
            alineacion='right', formato=formato_cup, formula=True,
        )
        escribir(
            ipv.cell(actual, 6), roturas, negrita=roturas > 0,
            color='FF9C1C1C' if roturas > 0 else texto_color, formato='#,##0.###',
        )
        escribir(ipv.cell(actual, 7), estado, negrita=True, color=color, fondo=fondo, tamano=9)
        ipv.row_dimensions[actual].height = 20
    fila_total_ipv = 5 + len(filas_ipv)
    for columna in range(1, 8):
        ipv.cell(fila_total_ipv, columna).fill = PatternFill('solid', fgColor=total_fill)
        ipv.cell(fila_total_ipv, columna).border = bordes
    escribir(ipv.cell(fila_total_ipv, 1), 'TOTAL', negrita=True, color=verde, fondo=total_fill, alineacion='left', sangria=1)
    escribir(ipv.cell(fila_total_ipv, 2), '', negrita=True, color=verde, fondo=total_fill)
    escribir(
        ipv.cell(fila_total_ipv, 3),
        f'=SUM(C5:C{fila_total_ipv - 1})' if filas_ipv else 0,
        negrita=True, color=verde, fondo=total_fill, formato='#,##0.###', formula=bool(filas_ipv),
    )
    escribir(ipv.cell(fila_total_ipv, 4), '', negrita=True, color=verde, fondo=total_fill)
    escribir(
        ipv.cell(fila_total_ipv, 5),
        f'=SUM(E5:E{fila_total_ipv - 1})' if filas_ipv else 0,
        negrita=True, color=verde, fondo=total_fill, alineacion='right', formato=formato_cup, formula=bool(filas_ipv),
    )
    escribir(
        ipv.cell(fila_total_ipv, 6),
        f'=SUM(F5:F{fila_total_ipv - 1})' if filas_ipv else 0,
        negrita=True, color=verde, fondo=total_fill, formato='#,##0.###', formula=bool(filas_ipv),
    )
    escribir(ipv.cell(fila_total_ipv, 7), '', negrita=True, color=verde, fondo=total_fill)
    ipv.row_dimensions[fila_total_ipv].height = 24
    if filas_ipv:
        ipv.auto_filter.ref = f'A4:G{fila_total_ipv - 1}'

    hoja_facturas = libro.create_sheet('Facturas')
    preparar_hoja(hoja_facturas, [10, 10, 18, 24, 10, 18])
    banda(hoja_facturas, 1, 1, 6, 'FACTURAS DEL TURNO', tamano=16)
    hoja_facturas.row_dimensions[1].height = 34
    metadato(hoja_facturas, 2, 1, 6, f'Fecha: {turno.fecha:%d/%m/%Y}   ·   {len(filas_facturas)} facturas')
    hoja_facturas.row_dimensions[2].height = 22
    hoja_facturas.row_dimensions[3].height = 8
    encabezado(hoja_facturas, 4, ['N.º', 'Hora', 'Mesa', 'Forma de pago', 'Moneda', 'Total'])
    for indice, factura in enumerate(filas_facturas):
        actual = 5 + indice
        escribir(hoja_facturas.cell(actual, 1), factura['numero'], formato='0')
        escribir(hoja_facturas.cell(actual, 2), factura['hora'])
        escribir(hoja_facturas.cell(actual, 3), factura['mesa'], alineacion='left', sangria=1)
        escribir(hoja_facturas.cell(actual, 4), factura['pago'], alineacion='left', sangria=1)
        escribir(
            hoja_facturas.cell(actual, 5), factura['moneda'], negrita=True,
            color='FFB7791F' if factura['moneda'] == 'USD' else verde,
        )
        escribir(
            hoja_facturas.cell(actual, 6), Decimal(factura['total']),
            alineacion='right',
            formato='#,##0.00" USD"' if factura['moneda'] == 'USD' else formato_cup,
        )
        hoja_facturas.row_dimensions[actual].height = 20
    if not filas_facturas:
        hoja_facturas.merge_cells('A5:F5')
        escribir(hoja_facturas['A5'], 'Aún no hay facturas en este turno.', color=gris_color)
    else:
        fila_total_facturas = 5 + len(filas_facturas)
        for columna in range(1, 7):
            hoja_facturas.cell(fila_total_facturas, columna).fill = PatternFill('solid', fgColor=total_fill)
            hoja_facturas.cell(fila_total_facturas, columna).border = bordes
        for columna in range(1, 6):
            escribir(
                hoja_facturas.cell(fila_total_facturas, columna),
                'TOTAL CUP' if columna == 4 else '',
                negrita=True, color=verde, fondo=total_fill,
                alineacion='right' if columna == 4 else 'center',
                sangria=1 if columna == 4 else 0,
            )
        escribir(
            hoja_facturas.cell(fila_total_facturas, 6),
            f'=SUMIF(E5:E{fila_total_facturas - 1},"CUP",F5:F{fila_total_facturas - 1})',
            negrita=True, color=verde, fondo=total_fill, alineacion='right', formato=formato_cup, formula=True,
        )
        hoja_facturas.row_dimensions[fila_total_facturas].height = 24
        hoja_facturas.auto_filter.ref = f'A4:F{fila_total_facturas - 1}'

    rechazados = libro.create_sheet('Rechazados')
    preparar_hoja(rechazados, [20, 14, 28, 10, 26, 22], color='FF9C1C1C')
    banda(rechazados, 1, 1, 6, 'DETALLE DE PLATOS RECHAZADOS', tamano=16)
    rechazados.row_dimensions[1].height = 34
    metadato(rechazados, 2, 1, 6, f'Fecha: {turno.fecha:%d/%m/%Y}   ·   {len(datos["rechazos"])} registros')
    rechazados.row_dimensions[2].height = 22
    rechazados.row_dimensions[3].height = 8
    encabezado(rechazados, 4, ['Fecha', 'Mesa', 'Plato', 'Cantidad', 'Motivo', 'Reportado por'])
    for indice, rechazo in enumerate(datos['rechazos']):
        actual = 5 + indice
        escribir(rechazados.cell(actual, 1), rechazo['creado_en'])
        escribir(rechazados.cell(actual, 2), rechazo['mesa'], alineacion='left', sangria=1)
        escribir(rechazados.cell(actual, 3), rechazo['plato'], negrita=True, alineacion='left', sangria=1)
        escribir(rechazados.cell(actual, 4), Decimal(rechazo['cantidad']), negrita=True, color='FFB7791F', formato='#,##0.###')
        escribir(rechazados.cell(actual, 5), rechazo['motivo'], alineacion='left', sangria=1, envolver=True)
        escribir(rechazados.cell(actual, 6), rechazo['reportado_por'], alineacion='left', sangria=1)
        rechazados.row_dimensions[actual].height = 20
    if not datos['rechazos']:
        rechazados.merge_cells('A5:F5')
        escribir(rechazados['A5'], 'No se reportaron platos rechazados en este turno.', color=gris_color)
    else:
        fila_total_rechazados = 5 + len(datos['rechazos'])
        for columna in range(1, 7):
            rechazados.cell(fila_total_rechazados, columna).fill = PatternFill('solid', fgColor=total_fill)
            rechazados.cell(fila_total_rechazados, columna).border = bordes
        for columna in (1, 2, 3, 5, 6):
            escribir(
                rechazados.cell(fila_total_rechazados, columna),
                'TOTAL' if columna == 1 else '',
                negrita=True, color=verde, fondo=total_fill, alineacion='left',
                sangria=1 if columna == 1 else 0,
            )
        escribir(
            rechazados.cell(fila_total_rechazados, 4),
            f'=SUM(D5:D{fila_total_rechazados - 1})',
            negrita=True, color=verde, fondo=total_fill, formato='#,##0.###', formula=True,
        )
        rechazados.row_dimensions[fila_total_rechazados].height = 24
        rechazados.auto_filter.ref = f'A4:F{fila_total_rechazados - 1}'

    buffer = BytesIO()
    libro.save(buffer)
    respuesta = HttpResponse(
        buffer.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    respuesta['Content-Disposition'] = f'attachment; filename="ipv_turno_{turno.pk}.xlsx"'
    return respuesta
