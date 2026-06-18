from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.db import transaction
from django.template.loader import render_to_string
from django.conf import settings
import logging

from .email_utils import send_email

logger = logging.getLogger(__name__)


@receiver(post_save, sender=User)
def send_welcome_email(sender, instance, created, **kwargs):
    """Send a welcome email when a new `User` is created.

    This uses `transaction.on_commit` so the email is sent only after the
    user creation transaction is committed (avoids sending on rolled-back tx).
    """
    if not created:
        return
    to = getattr(instance, 'email', None)
    if not to:
        return

    subject = getattr(settings, 'WELCOME_EMAIL_SUBJECT', 'Ласкаво просимо в Dieller Bus')
    try:
        html = render_to_string('emails/welcome.html', {'user': instance})
    except Exception:
        html = f'<p>Вітаємо, {instance.username}!</p><p>Дякуємо за реєстрацію.</p>'

    def _send():
        try:
            # use simple signature: send_email(to_email, subject, html_message)
            send_email(to, subject, html, fail_silently=False, async_send=True)
        except Exception:
            logger.exception('Failed to send welcome email to %s', to)

    try:
        transaction.on_commit(_send)
    except Exception:
        # if transaction management not available, fallback to immediate send
        _send()
