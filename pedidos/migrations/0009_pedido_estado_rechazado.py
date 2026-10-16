from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pedidos', '0008_factura_cambio_cup_factura_cambio_usd_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='pedido',
            name='estado',
            field=models.CharField(
                choices=[
                    ('por_confirmar', 'Esperando a la mesera'),
                    ('rechazado', 'Rechazado por la mesera'),
                    ('pendiente', 'Pendiente'),
                    ('en_preparacion', 'En preparación'),
                    ('listo', 'Listo'),
                    ('servido', 'Servido'),
                    ('entregado', 'Entregado'),
                    ('cerrado', 'Cerrado'),
                ],
                default='pendiente',
                max_length=20,
            ),
        ),
    ]
