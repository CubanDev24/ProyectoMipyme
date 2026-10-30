from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pedidos', '0009_pedido_estado_rechazado'),
    ]

    operations = [
        migrations.AddField(
            model_name='pedido',
            name='cancelacion_solicitada',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='pedido',
            name='motivo_cancelacion',
            field=models.CharField(blank=True, default='', max_length=300),
        ),
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
                    ('cancelado', 'Cancelado'),
                    ('cerrado', 'Cerrado'),
                ],
                default='pendiente',
                max_length=20,
            ),
        ),
    ]
