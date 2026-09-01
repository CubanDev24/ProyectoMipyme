from django.contrib import messages
from django.contrib.auth import logout, login, authenticate
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
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


@require_http_methods(['POST'])
def login_view(request):
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
    from carta.models import Plato
    return render(request, 'usuarios/dashboard.html', {
        'turno': turno,
        'notificaciones': notificaciones,
        'plato_options': Plato.objects.select_related('categoria').order_by('categoria__nombre', 'nombre'),
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
    password = request.POST.get('password', '')
    role = request.POST.get('role')
    if not username or not password or role not in dict(Usuario.ROLE_CHOICES):
        messages.error(request, 'Completa los datos del usuario.')
        return redirect('usuarios:dashboard')

    if Usuario.objects.filter(username__iexact=username).exists():
        messages.error(request, f'El usuario {username} ya existe.')
        return redirect('usuarios:dashboard')

    Usuario.objects.create_user(username=username, password=password, role=role)
    messages.success(request, f'Usuario {username} creado con rol {role}.')
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
def configurar_carta_turno_view(request):
    if request.user.role != 'administrador':
        messages.error(request, 'Solo el administrador puede configurar la carta del turno.')
        return redirect('usuarios:dashboard')

    turno = get_turno_abierto()
    if turno is None:
        turno = registrar_inicio_turno(request.user)

    plato_ids = request.POST.getlist('platos')
    platos = []
    if plato_ids:
        from carta.models import Plato
        platos = list(Plato.objects.filter(id__in=plato_ids))

    turno.platos.set(platos)
    messages.success(request, f'La carta del turno quedó configurada con {turno.platos.count()} platos.')
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
