from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventario', '0006_alter_insumo_stock_actual_alter_insumo_stock_maximo_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='insumo',
            name='stock_maximo',
            field=models.DecimalField(decimal_places=3, default=Decimal('0'), max_digits=12),
        ),
    ]