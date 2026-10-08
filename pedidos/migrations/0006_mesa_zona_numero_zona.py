from django.db import migrations, models


def asignar_zona_a_mesas_existentes(apps, schema_editor):
    Mesa = apps.get_model('pedidos', 'Mesa')
    Mesa.objects.all().update(numero_zona=models.F('numero'))


class Migration(migrations.Migration):

    dependencies = [
        ('pedidos', '0005_pedido_grupo_para_llevar_pedido_modalidad_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='mesa',
            name='zona',
            field=models.CharField(
                choices=[('salon', 'Mesa'), ('exteriores', 'Exteriores')],
                default='salon',
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name='mesa',
            name='numero_zona',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.RunPython(asignar_zona_a_mesas_existentes, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='mesa',
            constraint=models.UniqueConstraint(
                condition=models.Q(('numero_zona__isnull', False)),
                fields=('zona', 'numero_zona'),
                name='unique_mesa_zona_numero',
            ),
        ),
    ]
