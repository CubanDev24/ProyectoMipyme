from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pedidos', '0006_mesa_zona_numero_zona'),
    ]

    operations = [
        migrations.AddField(
            model_name='pedido',
            name='nombre_cliente',
            field=models.CharField(blank=True, default='', max_length=120),
        ),
        migrations.AddField(
            model_name='pedido',
            name='telefono_cliente',
            field=models.CharField(blank=True, default='', max_length=30),
        ),
    ]
