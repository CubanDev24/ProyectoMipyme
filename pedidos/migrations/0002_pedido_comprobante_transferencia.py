from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pedidos', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='pedido',
            name='comprobante_transferencia',
            field=models.ImageField(blank=True, null=True, upload_to='comprobantes_transferencia/%Y/%m/'),
        ),
    ]