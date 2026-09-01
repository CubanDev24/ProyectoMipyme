from decimal import Decimal
from django.db import models
from carta.models import Plato


class Insumo(models.Model):
    """Un producto/ingrediente de inventario (harina, pollo, refresco embotellado, etc)."""
    UNIDAD_CHOICES = [
        ('unidad', 'Unidad'),
        ('kg', 'Kilogramo'),
        ('g', 'Gramo'),
        ('l', 'Litro'),
        ('ml', 'Mililitro'),
    ]
    nombre = models.CharField(max_length=150, unique=True)
    unidad = models.CharField(max_length=10, choices=UNIDAD_CHOICES, default='unidad')
    stock_actual = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    stock_minimo = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['nombre']
        verbose_name = 'Insumo'
        verbose_name_plural = 'Insumos'

    def __str__(self):
        return self.nombre

    @property
    def stock_bajo(self):
        return self.stock_actual <= self.stock_minimo


class RecetaItem(models.Model):
    """Cuánto de un insumo consume una unidad de un plato (para descontar inventario)."""
    plato = models.ForeignKey(Plato, on_delete=models.CASCADE, related_name='receta')
    insumo = models.ForeignKey(Insumo, on_delete=models.CASCADE, related_name='usado_en')
    cantidad = models.DecimalField(max_digits=8, decimal_places=3, default=Decimal('1'))

    class Meta:
        unique_together = ('plato', 'insumo')
        verbose_name = 'Ingrediente de receta'
        verbose_name_plural = 'Ingredientes de receta'

    def __str__(self):
        return f'{self.plato} — {self.cantidad} {self.insumo.unidad} de {self.insumo.nombre}'


class TasaCambio(models.Model):
    """Tasa de cambio vigente: cuántos CUP equivalen a 1 USD. Se guarda histórico."""
    valor = models.DecimalField(max_digits=8, decimal_places=2)
    actualizada_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-actualizada_en']
        verbose_name = 'Tasa de cambio'
        verbose_name_plural = 'Tasas de cambio'

    def __str__(self):
        return f'1 USD = {self.valor} CUP ({self.actualizada_en:%d/%m/%Y %H:%M})'

    @classmethod
    def actual(cls):
        obj = cls.objects.first()
        return obj.valor if obj else None
