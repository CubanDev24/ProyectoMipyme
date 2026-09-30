from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pedidos', '0002_pedido_comprobante_transferencia'),
    ]

    operations = [
        migrations.AlterField(
            model_name='pedido',
            name='estado',
            field=models.CharField(
                choices=[
                    ('por_confirmar', 'Esperando a la mesera'),
                    ('pendiente', 'Pendiente'),
                    ('en_preparacion', 'En preparación'),
                    ('listo', 'Listo'),
                    ('servido', 'Servido'),
                    ('cerrado', 'Cerrado'),
                ],
                default='pendiente',
                max_length=20,
            ),
        ),
    ]