# Generated manually to add currency and discount_percent to Ticket
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0014_trip_is_international_tripdayavailability'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticket',
            name='currency',
            field=models.CharField(default='UAH', max_length=10),
        ),
        migrations.AddField(
            model_name='ticket',
            name='discount_percent',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
