import uuid

from django.db import models
from carta.models import Plato

class Mesa(models.Model):
    ZONA_CHOICES = [
        ('salon', 'Mesa'),
        ('exteriores', 'Exteriores'),
    ]

    numero = models.PositiveIntegerField(unique=True)
    zona = models.CharField(max_length=12, choices=ZONA_CHOICES, default='salon')
    numero_zona = models.PositiveIntegerField(null=True, blank=True)
    activa = models.BooleanField(default=True)
    abierta = models.BooleanField(default=False)
    sesion_id = models.UUIDField(default=uuid.uuid4, editable=False)

    class Meta:
        ordering = ['numero']
        verbose_name = 'Mesa'
        verbose_name_plural = 'Mesas'
        constraints = [
            models.UniqueConstraint(
                fields=['zona', 'numero_zona'],
                condition=models.Q(numero_zona__isnull=False),
                name='unique_mesa_zona_numero',
            ),
        ]

    @property
    def etiqueta(self):
        numero_visible = self.numero_zona or self.numero
        prefijo = 'Exteriores' if self.zona == 'exteriores' else 'Mesa'
        return f'{prefijo} {numero_visible}'

    def __str__(self):
        return self.etiqueta

class Pedido(models.Model):
    MODALIDAD_CHOICES = [
        ('mesa', 'Mesa'),
        ('para_llevar', 'Para llevar'),
    ]
    DESTINO_CHOICES = [
        ('cocina', 'Cocina'),
        ('barra', 'Barra'),
    ]
    ESTADO_CHOICES = [
        ('por_confirmar', 'Esperando a la mesera'),
        ('pendiente', 'Pendiente'),
        ('en_preparacion', 'En preparación'),
        ('listo', 'Listo'),
        ('servido', 'Servido'),
        ('entregado', 'Entregado'),
        ('cerrado', 'Cerrado'),
    ]
    mesa = models.ForeignKey(Mesa, on_delete=models.PROTECT, related_name='pedidos', null=True, blank=True)
    modalidad = models.CharField(max_length=12, choices=MODALIDAD_CHOICES, default='mesa')
    grupo_para_llevar = models.UUIDField(null=True, blank=True, db_index=True)
    nombre_cliente = models.CharField(max_length=120, blank=True, default='')
    telefono_cliente = models.CharField(max_length=30, blank=True, default='')
    destino = models.CharField(max_length=10, choices=DESTINO_CHOICES, default='cocina')
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='pendiente')
    nota = models.TextField(blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)
    cuenta_solicitada = models.BooleanField(default=False)
    cerrado_en = models.DateTimeField(null=True, blank=True)
    inventario_descontado = models.BooleanField(default=False)
    sesion_id = models.UUIDField(null=True, editable=False)
    forma_pago_preseleccionada = models.CharField(max_length=20, blank=True, default='')
    tasa_cambio_preseleccionada = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    comprobante_transferencia = models.ImageField(upload_to='comprobantes_transferencia/%Y/%m/', blank=True, null=True)

    class Meta:
        ordering = ['-creado_en']
        verbose_name = 'Pedido'
        verbose_name_plural = 'Pedidos'

    def __str__(self):
        etiqueta = self.mesa.etiqueta if self.mesa_id else 'Para llevar'
        return f'{etiqueta} — Pedido #{self.pk}'

    def total(self):
        return sum(item.subtotal() for item in self.items.all())

class ItemPedido(models.Model):
    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name='items')
    plato = models.ForeignKey(Plato, on_delete=models.PROTECT)
    cantidad = models.PositiveIntegerField(default=1)
    nota = models.CharField(max_length=200, blank=True)

    def subtotal(self): return self.plato.precio * self.cantidad
    def __str__(self): return f'{self.cantidad}x {self.plato.nombre}'


class Factura(models.Model):
    """Registro de cobro de una cuenta, generado por la cajera al cerrar el pedido."""
    FORMA_PAGO_CHOICES = [
        ('transferencia', 'Transferencia'),
        ('efectivo_cup', 'Efectivo CUP'),
        ('mixto', 'Mixto (mitad transferencia, mitad efectivo CUP)'),
        ('usd', 'USD'),
    ]
    pedido = models.OneToOneField(Pedido, on_delete=models.PROTECT, related_name='factura')
    mesa_numero = models.PositiveIntegerField(null=True, blank=True)
    forma_pago = models.CharField(max_length=20, choices=FORMA_PAGO_CHOICES)
    total_cup = models.DecimalField(max_digits=10, decimal_places=2)
    monto_efectivo_cup = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    monto_transferencia_cup = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    monto_usd = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tasa_cambio = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    mesera_nombre = models.CharField(max_length=100, blank=True, default='')
    cajera_nombre = models.CharField(max_length=100, blank=True, default='')
    items_snapshot = models.JSONField(default=list, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creado_en']
        verbose_name = 'Factura'
        verbose_name_plural = 'Facturas'

    def __str__(self):
        etiqueta = self.pedido.mesa.etiqueta if self.pedido.mesa_id else 'Para llevar'
        return f'Factura #{self.pk} — {etiqueta} — {self.total_cup} CUP'
