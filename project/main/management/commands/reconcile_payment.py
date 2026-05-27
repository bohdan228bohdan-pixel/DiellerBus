from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
import logging

from main.models import Payment, Ticket

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Reconcile a pending payment or mark a ticket as paid. Use --ticket or --payment.'

    def add_arguments(self, parser):
        parser.add_argument('--ticket', type=int, dest='ticket_id', help='Ticket id to mark paid')
        parser.add_argument('--payment', type=int, dest='payment_id', help='Payment id to mark success')
        parser.add_argument('--force', action='store_true', dest='force', help='Apply without interactive confirmation')
        parser.add_argument('--send-email', action='store_true', dest='send_email', help='Send ticket email after marking paid')

    def handle(self, *args, **options):
        ticket_id = options.get('ticket_id')
        payment_id = options.get('payment_id')
        force = bool(options.get('force'))
        send_email = bool(options.get('send_email'))

        if not ticket_id and not payment_id:
            raise CommandError('Provide --ticket or --payment')

        if payment_id:
            try:
                p = Payment.objects.get(pk=payment_id)
            except Payment.DoesNotExist:
                raise CommandError(f'Payment {payment_id} not found')
            self.stdout.write(f'Payment {p.id}: status={p.status} ticket_id={getattr(p.ticket, "id", None)} amount={p.amount}')
            if not force:
                confirm = input('Mark this payment as success? [y/N]: ').strip().lower()
                if confirm != 'y':
                    self.stdout.write('Aborted')
                    return
            p.status = 'success'
            p.save()
            if getattr(p, 'ticket', None):
                try:
                    t = p.ticket
                    t.paid = True
                    t.save(update_fields=['paid'])
                    self.stdout.write(self.style.SUCCESS(f'Ticket {t.id} marked paid'))
                    if send_email:
                        try:
                            from main.views import _send_ticket_email
                            _send_ticket_email(t, p)
                            self.stdout.write('Ticket email sent')
                        except Exception:
                            logger.exception('Failed to send ticket email for ticket %s', t.id)
                except Exception:
                    logger.exception('Failed to update ticket for payment %s', p.id)
            return

        if ticket_id:
            try:
                t = Ticket.objects.get(pk=ticket_id)
            except Ticket.DoesNotExist:
                raise CommandError(f'Ticket {ticket_id} not found')
            self.stdout.write(f'Ticket {t.id}: paid={t.paid} total_price={getattr(t, "total_price", None)}')
            if not force:
                confirm = input('Mark this ticket as paid and create success Payment? [y/N]: ').strip().lower()
                if confirm != 'y':
                    self.stdout.write('Aborted')
                    return
            # find existing payment or create one
            p = Payment.objects.filter(ticket=t).order_by('-created_at').first()
            if p:
                p.status = 'success'
                p.save()
            else:
                p = Payment.objects.create(ticket=t, user=t.user if getattr(t, 'user', None) else None, provider='manual', provider_payment_id='manual_reconcile', amount=(t.total_price or 0), currency=(getattr(t, 'currency', None) or 'UAH'), status='success', data={'by': 'management_command'})
            t.paid = True
            t.save(update_fields=['paid'])
            self.stdout.write(self.style.SUCCESS(f'Ticket {t.id} marked paid and payment recorded (id={p.id})'))
            if send_email:
                try:
                    from main.views import _send_ticket_email
                    _send_ticket_email(t, p)
                    self.stdout.write('Ticket email sent')
                except Exception:
                    logger.exception('Failed to send ticket email for ticket %s', t.id)
            return
