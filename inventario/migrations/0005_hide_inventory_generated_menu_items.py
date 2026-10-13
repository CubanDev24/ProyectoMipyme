from django.db import migrations
from django.db.models import Q


def unpublish_legacy_menu_items(apps, schema_editor):
    Insumo = apps.get_model('inventario', 'Insumo')
    Plato = apps.get_model('carta', 'Plato')
    database = schema_editor.connection.alias

    for plato in Plato.objects.using(database).all().iterator():
        if plato.descripcion != f'Producto del inventario: {plato.nombre}':
            continue
        linked_inventory_item = Insumo.objects.using(database).filter(
            nombre__iexact=plato.nombre,
        ).filter(
            Q(tipo_inventario='restaurante') | Q(tipo_inventario__isnull=True),
        ).exists()
        if linked_inventory_item and plato.disponible:
            Plato.objects.using(database).filter(pk=plato.pk).update(disponible=False)

    Plato.objects.using(database).filter(
        disponible=True,
        receta__isnull=True,
    ).update(disponible=False)


class Migration(migrations.Migration):

    dependencies = [
        ('carta', '0001_initial'),
        ('inventario', '0004_insumo_tipo_inventario_and_more'),
    ]

    operations = [
        migrations.RunPython(unpublish_legacy_menu_items, migrations.RunPython.noop),
    ]
