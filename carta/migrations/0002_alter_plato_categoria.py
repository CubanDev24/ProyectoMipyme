from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('carta', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='plato',
            name='categoria',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name='platos',
                to='carta.categoria',
            ),
        ),
    ]
