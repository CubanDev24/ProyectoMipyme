from decimal import Decimal

from django.db.models.signals import post_save
from django.dispatch import receiver

from carta.models import Plato
from .models import Insumo, RecetaItem


@receiver(post_save, sender=Insumo)
def desactivar_platos_sin_stock(sender, instance, **kwargs):
    if Decimal(instance.stock_actual) <= 0:
        Plato.objects.filter(
            disponible=True,
            receta__insumo_id=instance.pk,
        ).update(disponible=False)


@receiver(post_save, sender=RecetaItem)
def desactivar_plato_con_insumo_agotado(sender, instance, **kwargs):
    Plato.objects.filter(
        pk=instance.plato_id,
        disponible=True,
        receta__insumo__stock_actual__lte=0,
    ).update(disponible=False)