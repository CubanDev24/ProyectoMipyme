from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('usuarios', '0004_configuracionwifi'),
    ]

    operations = [
        migrations.AddField(
            model_name='turno',
            name='continuidad_confirmada',
            field=models.BooleanField(default=False),
        ),
    ]