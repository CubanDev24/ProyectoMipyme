from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('carta', '0002_alter_plato_categoria'),
    ]

    operations = [
        migrations.AddField(
            model_name='plato',
            name='destino',
            field=models.CharField(
                choices=[('cocina', 'Cocina'), ('barra', 'Barra')],
                default='cocina',
                max_length=10,
            ),
        ),
    ]
