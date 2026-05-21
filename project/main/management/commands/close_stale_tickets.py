from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from main.models import SupportTicket, SupportMessage
from django.core.mail import send_mail
from django.conf import settings


class Command(BaseCommand):
    help = 'Close support tickets where the last message was from admin and the user did not reply within timeout (minutes).'

    def add_arguments(self, parser):
        parser.add_argument('--minutes', type=int, default=20, help='Inactivity timeout in minutes')

    def handle(self, *args, **options):
        minutes = options.get('minutes') or 20
        cutoff = timezone.now() - timedelta(minutes=minutes)
        qs = SupportTicket.objects.filter(status=SupportTicket.STATUS_IN_PROGRESS, is_archived=False, last_message_at__lte=cutoff)
        closed = 0
        for ticket in qs:
            # find last message
            last = ticket.messages.order_by('-created_at').first()
            if not last:
                continue
            # only close if last message was from admin (i.e., staff) and no user reply
            if last.is_from_admin:
                # close ticket
                ticket.status = SupportTicket.STATUS_CLOSED
                ticket.save()
                # create system message
                text = 'Ви довго не відповідали — звернення автоматично закрите. Якщо питання залишилось, будь ласка, надішліть звернення ще раз.'
                SupportMessage.objects.create(ticket=ticket, sender=None, text=text, is_from_admin=True)
                # send email notification (best-effort)
                try:
                    send_mail(
                        'Ваше звернення закрите — Dieller Bus',
                        text,
                        getattr(settings, 'DEFAULT_FROM_EMAIL', None),
                        [ticket.user.email],
                        fail_silently=True,
                    )
                except Exception:
                    pass
                closed += 1
        self.stdout.write(self.style.SUCCESS(f'Closed {closed} tickets (timeout={minutes}m)'))
