from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from carta.models import Plato


class Usuario(AbstractUser):
    ROLE_CHOICES = [
        ('administrador', 'Administrador'),
        ('cocina', 'Cocina'),
        ('mesera', 'Mesera'),
        ('cajera', 'Cajera'),
    ]

    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='mesera')
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


class Turno(models.Model):
    ESTADO_CHOICES = [
        ('abierto', 'Abierto'),
        ('cerrado', 'Cerrado'),
    ]

    fecha = models.DateField(default=timezone.localdate)
    apertura = models.DateTimeField(auto_now_add=True)
    cierre = models.DateTimeField(null=True, blank=True)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='abierto')
    usuarios = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='turnos', blank=True)
    cantidad_mesas = models.PositiveIntegerField(default=10)
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
        return Factura.objects.filter(creado_en__date=self.fecha)

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
    fecha = fecha or timezone.localdate()
    return Turno.objects.filter(fecha=fecha, estado='abierto').order_by('-apertura').first()


def crear_mesas_del_turno(turno):
    if turno is None:
        return []
    from pedidos.models import Mesa
    cantidad = max(int(turno.cantidad_mesas), 1)
    existentes = set(Mesa.objects.filter(numero__lte=cantidad).values_list('numero', flat=True))
    for numero in range(1, cantidad + 1):
        if numero not in existentes:
            Mesa.objects.create(numero=numero, activa=True, abierta=False)
    return Mesa.objects.filter(activa=True, numero__lte=cantidad).order_by('numero')


def mesas_del_turno(turno=None):
    turno = turno or get_turno_abierto()
    if turno is None:
        return Mesa.objects.none()
    from pedidos.models import Mesa
    cantidad = max(turno.cantidad_mesas, 1)
    return crear_mesas_del_turno(turno)


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


def configurar_turno(turno, cantidad_mesas=None, platos=None):
    if turno is None:
        return None
    if cantidad_mesas is not None:
        turno.cantidad_mesas = max(int(cantidad_mesas), 1)
    if platos is not None:
        turno.platos.set(platos)
    turno.save(update_fields=['cantidad_mesas'])
    return turno


def cerrar_turno(usuario, observaciones=''):
    if usuario is None or getattr(usuario, 'role', None) != 'cajera':
        raise PermissionError('Solo la cajera puede cerrar el turno.')

    turno = get_turno_abierto()
    if turno is None:
        raise ValueError('No hay un turno abierto para cerrar.')

    from decimal import Decimal
    from django.db.models import Sum
    from pedidos.models import Factura

    facturas = Factura.objects.filter(creado_en__date=turno.fecha)
    total = facturas.aggregate(total=Sum('total_cup'))['total'] or Decimal('0')
    efectivo = facturas.aggregate(efectivo=Sum('monto_efectivo_cup'))['efectivo'] or Decimal('0')
    transferencia = facturas.aggregate(transferencia=Sum('monto_transferencia_cup'))['transferencia'] or Decimal('0')
    usd = facturas.aggregate(usd=Sum('monto_usd'))['usd'] or Decimal('0')
    usd_cup_equivalente = facturas.aggregate(cup=Sum('total_cup'))['cup'] or Decimal('0')

    turno.cierre = timezone.now()
    turno.estado = 'cerrado'
    turno.observaciones = observaciones
    turno.resumen = (
        f'Fecha: {turno.fecha:%d/%m/%Y}\n'
        f'Ventas: {facturas.count()}\n'
        f'USD: {usd:.2f} USD\n'
        f'Efectivo CUP: {efectivo:.2f} CUP\n'
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
            mensaje=f'La cajera {usuario.username} cerró el turno del {turno.fecha:%d/%m/%Y}.\n\n{turno.resumen}',
            turno=turno,
        )
    return turno
