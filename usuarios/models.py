from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone

from carta.models import Plato


class Usuario(AbstractUser):
    ROLE_CHOICES = [
        ('administrador', 'Administrador'),
        ('cocina', 'Cocina'),
        ('mesera', 'Mesera'),
        ('cajera', 'Cajera'),
    ]

    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='administrador')
    telefono = models.CharField(max_length=30, blank=True)
    activo = models.BooleanField(default=True)
    ultimo_login_turno = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['username']
        verbose_name = 'Usuario'
        verbose_name_plural = 'Usuarios'

    def __str__(self):
        return f'{self.username} ({self.get_role_display()})'

    @property
    def es_administrador(self):
        return self.role == 'administrador'

    @property
    def es_cocina(self):
        return self.role == 'cocina'

    @property
    def es_mesera(self):
        return self.role == 'mesera'

    @property
    def es_cajera(self):
        return self.role == 'cajera'
    

class ConfiguracionWifi(models.Model):
    ssid = models.CharField(max_length=32, blank=True)
    password = models.CharField(max_length=63, blank=True)

    class Meta:
        verbose_name = 'Configuración de Wi-Fi'
        verbose_name_plural = 'Configuración de Wi-Fi'

    def __str__(self):
        return self.ssid or 'Wi-Fi del local'


class Turno(models.Model):
    ESTADO_CHOICES = [
        ('abierto', 'Abierto'),
        ('cerrado', 'Cerrado'),
    ]

    fecha = models.DateField(default=timezone.localdate)
    apertura = models.DateTimeField(auto_now_add=True)
    cierre = models.DateTimeField(null=True, blank=True)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='abierto')
    continuidad_confirmada = models.BooleanField(default=False)
    fondo_inicial_cup = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    usuarios = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='turnos', blank=True)
    cantidad_mesas = models.PositiveIntegerField(default=10)
    cantidad_mesas_exteriores = models.PositiveIntegerField(default=0)
    platos = models.ManyToManyField(Plato, related_name='turnos', blank=True)
    resumen = models.TextField(blank=True, default='')
    observaciones = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-fecha', '-apertura']
        verbose_name = 'Turno'
        verbose_name_plural = 'Turnos'

    def __str__(self):
        return f'Turno {self.fecha:%d/%m/%Y} - {self.get_estado_display()}'

    @property
    def facturas_queryset(self):
        from pedidos.models import Factura
        cierre = self.cierre or timezone.now()
        return Factura.objects.filter(creado_en__gte=self.apertura, creado_en__lte=cierre)

    @property
    def total_general_cup(self):
        return self.facturas_queryset.aggregate(total=Sum('total_cup'))['total'] or Decimal('0')

    @property
    def total_efectivo_cup(self):
        return self.facturas_queryset.aggregate(total=Sum('monto_efectivo_cup'))['total'] or Decimal('0')

    @property
    def total_transferencia_cup(self):
        return self.facturas_queryset.aggregate(total=Sum('monto_transferencia_cup'))['total'] or Decimal('0')

    @property
    def total_usd(self):
        return self.facturas_queryset.aggregate(total=Sum('monto_usd'))['total'] or Decimal('0')

    @property
    def cantidad_facturas(self):
        return self.facturas_queryset.count()

    @property
    def ipv(self):
        if self.cantidad_facturas == 0:
            return Decimal('0')
        return self.total_general_cup / Decimal(self.cantidad_facturas)

    @property
    def horas_abiertas(self):
        if self.cierre is None or self.apertura is None:
            return Decimal('0')
        delta = self.cierre - self.apertura
        segundos = delta.total_seconds()
        if segundos <= 0:
            return Decimal('0')
        if segundos < 60:
            return Decimal('0')
        return Decimal(str(max(segundos / 3600, 0)))

    @property
    def rendimiento_por_hora_cup(self):
        if self.horas_abiertas <= 0:
            return Decimal('0')
        return self.total_general_cup / self.horas_abiertas

    @property
    def resumen_financiero(self):
        return {
            'total_general_cup': self.total_general_cup,
            'total_efectivo_cup': self.total_efectivo_cup,
            'total_transferencia_cup': self.total_transferencia_cup,
            'total_usd': self.total_usd,
            'cantidad_facturas': self.cantidad_facturas,
            'ipv': self.ipv,
            'horas_abiertas': self.horas_abiertas,
            'rendimiento_por_hora_cup': self.rendimiento_por_hora_cup,
        }


class ProductoIPVTurno(models.Model):
    turno = models.ForeignKey(Turno, on_delete=models.CASCADE, related_name='productos_ipv')
    plato = models.ForeignKey(Plato, on_delete=models.PROTECT, related_name='registros_ipv')
    cantidad_inicial = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal('0'))
    entradas = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal('0'))
    merma_rotura = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal('0'))

    class Meta:
        ordering = ['plato__nombre']
        constraints = [
            models.UniqueConstraint(
                fields=['turno', 'plato'],
                name='uniq_producto_ipv_turno_plato',
            ),
        ]
        verbose_name = 'Producto IPV del turno'
        verbose_name_plural = 'Productos IPV del turno'

    def __str__(self):
        return f'{self.plato.nombre} - Turno {self.turno_id}'


class Notificacion(models.Model):
    destinatario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notificaciones')
    asunto = models.CharField(max_length=200)
    mensaje = models.TextField()
    leida = models.BooleanField(default=False)
    turno = models.ForeignKey('Turno', on_delete=models.SET_NULL, null=True, blank=True, related_name='notificaciones')
    creada_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creada_en']
        verbose_name = 'Notificación'
        verbose_name_plural = 'Notificaciones'

    def __str__(self):
        return f'{self.destinatario} - {self.asunto}'


def get_turno_abierto(fecha=None):
    turnos = Turno.objects.filter(estado='abierto')
    if fecha is not None:
        turnos = turnos.filter(fecha=fecha)
    return turnos.order_by('-apertura').first()


def crear_mesas_del_turno(turno):
    if turno is None:
        return []
    from pedidos.models import Mesa
    cantidad = max(int(turno.cantidad_mesas), 1)
    mesas_salon = list(Mesa.objects.filter(zona='salon'))
    existentes_salon = {mesa.numero_zona or mesa.numero for mesa in mesas_salon}
    siguiente_numero = (Mesa.objects.order_by('-numero').values_list('numero', flat=True).first() or 0) + 1
    for numero_zona in range(1, cantidad + 1):
        if numero_zona not in existentes_salon:
            numero = numero_zona
            if Mesa.objects.filter(numero=numero).exists():
                while Mesa.objects.filter(numero=siguiente_numero).exists():
                    siguiente_numero += 1
                numero = siguiente_numero
                siguiente_numero += 1
            Mesa.objects.create(
                numero=numero,
                zona='salon',
                numero_zona=numero_zona,
                activa=True,
                abierta=False,
            )

    cantidad_exteriores = int(turno.cantidad_mesas_exteriores)
    exteriores_existentes = set(
        Mesa.objects.filter(zona='exteriores').values_list('numero_zona', flat=True)
    )
    for numero_zona in range(1, cantidad_exteriores + 1):
        if numero_zona not in exteriores_existentes:
            while Mesa.objects.filter(numero=siguiente_numero).exists():
                siguiente_numero += 1
            Mesa.objects.create(
                numero=siguiente_numero,
                zona='exteriores',
                numero_zona=numero_zona,
                activa=True,
                abierta=False,
            )
            siguiente_numero += 1

    return Mesa.objects.filter(
        Q(activa=True, zona='salon', numero_zona__lte=cantidad)
        | Q(activa=True, zona='salon', numero_zona__isnull=True, numero__lte=cantidad)
        | Q(activa=True, zona='exteriores', numero_zona__lte=cantidad_exteriores)
    ).order_by('zona', 'numero_zona')


def mesas_del_turno(turno=None):
    turno = turno or get_turno_abierto()
    if turno is None:
        return Mesa.objects.none()
    from pedidos.models import Mesa
    crear_mesas_del_turno(turno)
    return Mesa.objects.filter(activa=True).order_by('zona', 'numero_zona', 'numero')


def registrar_inicio_turno(usuario):
    if usuario is None or not getattr(usuario, 'is_authenticated', False):
        return None

    turno = get_turno_abierto()
    if turno is None:
        turno = Turno.objects.create(fecha=timezone.localdate(), estado='abierto')

    turno.usuarios.add(usuario)
    usuario.ultimo_login_turno = timezone.now()
    usuario.save(update_fields=['ultimo_login_turno'])
    return turno


def configurar_turno(turno, cantidad_mesas=None, platos=None, cantidad_mesas_exteriores=None):
    if turno is None:
        return None
    if cantidad_mesas is not None:
        turno.cantidad_mesas = max(int(cantidad_mesas), 1)
    if cantidad_mesas_exteriores is not None:
        turno.cantidad_mesas_exteriores = max(int(cantidad_mesas_exteriores), 0)
    if platos is not None:
        turno.platos.set(platos)
    turno.save(update_fields=['cantidad_mesas', 'cantidad_mesas_exteriores'])
    return turno


def cerrar_turno(usuario, observaciones='', turno=None):
    if usuario is None or getattr(usuario, 'role', None) not in ('cajera', 'administrador'):
        raise PermissionError('Solo caja o el administrador pueden cerrar el turno.')

    turno = turno or get_turno_abierto()
    if turno is None:
        raise ValueError('No hay un turno abierto para cerrar.')
    if turno.estado != 'abierto':
        raise ValueError('El turno seleccionado ya está cerrado.')

    from decimal import Decimal
    from django.db.models import Sum
    from pedidos.models import Factura, Pedido

    cierre = timezone.now()
    pedidos_mesa_pendientes = Pedido.objects.filter(
        modalidad='mesa',
        mesa__abierta=True,
    ).exclude(estado__in=['cerrado', 'rechazado', 'cancelado'])
    if pedidos_mesa_pendientes.exists():
        raise ValueError('No se puede cerrar el turno: hay pedidos pendientes en una mesa.')

    tickets_sin_cobrar = Pedido.objects.filter(
        modalidad='para_llevar',
        creado_en__gte=turno.apertura,
        creado_en__lte=cierre,
        factura__isnull=True,
    ).exclude(estado__in=['rechazado', 'cancelado'])
    if tickets_sin_cobrar.exists():
        raise ValueError('No se puede cerrar el turno: hay tickets para llevar sin cobrar.')

    facturas = Factura.objects.filter(creado_en__gte=turno.apertura, creado_en__lte=cierre)
    total = facturas.aggregate(total=Sum('total_cup'))['total'] or Decimal('0')
    efectivo = facturas.aggregate(efectivo=Sum('monto_efectivo_cup'))['efectivo'] or Decimal('0')
    transferencia = facturas.aggregate(transferencia=Sum('monto_transferencia_cup'))['transferencia'] or Decimal('0')
    usd = facturas.aggregate(usd=Sum('monto_usd'))['usd'] or Decimal('0')
    usd_cup_equivalente = facturas.aggregate(cup=Sum('total_cup'))['cup'] or Decimal('0')
    efectivo_esperado = turno.fondo_inicial_cup + efectivo

    turno.cierre = cierre
    turno.estado = 'cerrado'
    turno.observaciones = observaciones
    turno.resumen = (
        f'Fecha: {turno.fecha:%d/%m/%Y}\n'
        f'Ventas: {facturas.count()}\n'
        f'USD: {usd:.2f} USD\n'
        f'Fondo inicial: {turno.fondo_inicial_cup:.2f} CUP\n'
        f'Efectivo CUP: {efectivo:.2f} CUP\n'
        f'Efectivo esperado: {efectivo_esperado:.2f} CUP\n'
        f'Transferencia CUP: {transferencia:.2f} CUP\n'
        f'Total general: {total:.2f} CUP\n'
        f'Total en USD: {usd_cup_equivalente:.2f} CUP equivalentes\n'
        f'Participantes: {", ".join(user.get_full_name() or user.username for user in turno.usuarios.all()) or "-"}'
    )
    turno.save()

    admin_users = Usuario.objects.filter(role='administrador', is_active=True)
    for admin in admin_users:
        Notificacion.objects.create(
            destinatario=admin,
            asunto='Cierre de turno realizado',
            mensaje=f'{usuario.get_role_display()} {usuario.username} cerró el turno del {turno.fecha:%d/%m/%Y}.\n\n{turno.resumen}',
            turno=turno,
        )
    return turno
