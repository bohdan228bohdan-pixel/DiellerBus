# Generated migration: add used & used_at to Ticket
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('main', '0023_supportticket_ticket'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticket',
            name='used',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='ticket',
            name='used_at',
            field=models.DateTimeField(null=True, blank=True),
        ),
    ]
