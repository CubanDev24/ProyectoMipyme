from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventario', '0002_insumo_costo_insumo_stock_maximo_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='movimientoinventario',
            name='tipo',
            field=models.CharField(
                choices=[
                    ('entrada', 'Entrada'),
                    ('salida', 'Salida'),
                    ('merma', 'Merma por rotura'),
                ],
                max_length=10,
            ),
        ),
    ]