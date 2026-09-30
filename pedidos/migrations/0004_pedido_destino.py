from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pedidos', '0003_pedido_estado_por_confirmar'),
    ]

    operations = [
        migrations.AddField(
            model_name='pedido',
            name='destino',
            field=models.CharField(
                choices=[('cocina', 'Cocina'), ('barra', 'Barra')],
                default='cocina',
                max_length=10,
            ),
        ),
    ]