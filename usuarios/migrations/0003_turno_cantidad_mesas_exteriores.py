from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('usuarios', '0002_alter_usuario_role'),
    ]

    operations = [
        migrations.AddField(
            model_name='turno',
            name='cantidad_mesas_exteriores',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
