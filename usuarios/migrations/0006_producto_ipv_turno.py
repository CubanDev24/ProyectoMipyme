import decimal

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('carta', '0003_plato_destino'),
        ('usuarios', '0005_turno_continuidad_confirmada'),
    ]

    operations = [
        migrations.AddField(
            model_name='turno',
            name='fondo_inicial_cup',
            field=models.DecimalField(decimal_places=2, default=decimal.Decimal('0'), max_digits=12),
        ),
        migrations.CreateModel(
            name='ProductoIPVTurno',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cantidad_inicial', models.DecimalField(decimal_places=3, default=decimal.Decimal('0'), max_digits=12)),
                ('entradas', models.DecimalField(decimal_places=3, default=decimal.Decimal('0'), max_digits=12)),
                ('merma_rotura', models.DecimalField(decimal_places=3, default=decimal.Decimal('0'), max_digits=12)),
                ('plato', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='registros_ipv', to='carta.plato')),
                ('turno', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='productos_ipv', to='usuarios.turno')),
            ],
            options={
                'verbose_name': 'Producto IPV del turno',
                'verbose_name_plural': 'Productos IPV del turno',
                'ordering': ['plato__nombre'],
            },
        ),
        migrations.AddConstraint(
            model_name='productoipvturno',
            constraint=models.UniqueConstraint(fields=('turno', 'plato'), name='uniq_producto_ipv_turno_plato'),
        ),
    ]