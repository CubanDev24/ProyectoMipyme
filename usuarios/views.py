from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import logout, login, authenticate, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .models import Notificacion, Turno, Usuario, cerrar_turno, configurar_turno, get_turno_abierto, registrar_inicio_turno


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
    registrar_inicio_turno(user)
    return redirect(ROLE_HOME.get(user.role, 'usuarios:landing'))


@login_required
def dashboard(request):
    turno = get_turno_abierto()
    notificaciones = Notificacion.objects.filter(destinatario=request.user).order_by('-creada_en')[:10]
    usuarios = Usuario.objects.order_by('role', 'username') if request.user.role == 'administrador' else []
    return render(request, 'usuarios/dashboard.html', {
        'turno': turno,
        'notificaciones': notificaciones,
        'usuarios': usuarios,
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
        return redirect('usuarios:dashboard')

    if Usuario.objects.filter(username__iexact=username).exists():
        messages.error(request, f'El usuario {username} ya existe.')
        return redirect('usuarios:dashboard')

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
    return redirect('usuarios:dashboard')


@login_required
@require_http_methods(['POST'])
def cambiar_estado_usuario(request, user_id):
    if request.user.role != 'administrador':
        messages.error(request, 'Solo el administrador puede gestionar usuarios.')
        return redirect('usuarios:dashboard')

    usuario = get_object_or_404(Usuario, pk=user_id)
    if usuario == request.user:
        messages.error(request, 'No puedes desactivar tu propio usuario.')
        return redirect('usuarios:dashboard')

    usuario.is_active = not usuario.is_active
    usuario.activo = usuario.is_active
    usuario.save(update_fields=['is_active', 'activo'])
    estado = 'activado' if usuario.is_active else 'desactivado'
    messages.success(request, f'Usuario {usuario.username} {estado}.')
    return redirect('usuarios:dashboard')


@login_required
@require_http_methods(['POST'])
def editar_usuario(request, user_id):
    if request.user.role != 'administrador':
        messages.error(request, 'Solo el administrador puede gestionar usuarios.')
        return redirect('usuarios:dashboard')

    usuario = get_object_or_404(Usuario, pk=user_id)
    username = request.POST.get('username', '').strip()
    first_name = request.POST.get('first_name', '').strip()
    last_name = request.POST.get('last_name', '').strip()
    role = request.POST.get('role')
    password = request.POST.get('password', '')
    if not username or not first_name or not last_name or role not in dict(Usuario.ROLE_CHOICES):
        messages.error(request, 'Completa nombre, apellidos, usuario y rol.')
        return redirect('usuarios:dashboard')

    if Usuario.objects.filter(username__iexact=username).exclude(pk=usuario.pk).exists():
        messages.error(request, f'El usuario {username} ya existe.')
        return redirect('usuarios:dashboard')
    if usuario == request.user and role != 'administrador':
        messages.error(request, 'No puedes quitarte el rol de administrador a ti mismo.')
        return redirect('usuarios:dashboard')

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
    return redirect('usuarios:dashboard')


@login_required
@require_http_methods(['POST'])
def configurar_mesas_turno_view(request):
    if request.user.role != 'administrador':
        messages.error(request, 'Solo el administrador puede configurar las mesas del turno.')
        return redirect('usuarios:dashboard')

    turno = get_turno_abierto()
    if turno is None:
        turno = registrar_inicio_turno(request.user)

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
