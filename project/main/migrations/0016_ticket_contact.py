# Generated manually: add contact_email and contact_phone to Ticket
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0015_ticket_currency_discount'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticket',
            name='contact_email',
            field=models.EmailField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='ticket',
            name='contact_phone',
            field=models.CharField(blank=True, max_length=32, null=True),
        ),
    ]
