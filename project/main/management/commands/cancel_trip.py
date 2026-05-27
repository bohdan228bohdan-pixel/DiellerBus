from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.conf import settings
from django.template.loader import render_to_string
from django.core.mail import EmailMessage
import logging
import datetime

from main.models import Trip, TripDayAvailability, Ticket, Payment

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Cancel a Trip on a specific date, refund buyers and mark date unavailable.'

    def add_arguments(self, parser):
        parser.add_argument('--trip', type=int, required=True, help='Trip id to cancel')
        parser.add_argument('--date', type=str, required=True, help='Date to cancel (YYYY-MM-DD)')
        parser.add_argument('--dry-run', action='store_true', dest='dry_run', help='Do not modify DB, only preview')
        parser.add_argument('--send-emails', action='store_true', dest='send_emails', help='Send cancellation emails to affected users')
        parser.add_argument('--domain', type=str, dest='domain', help='Base domain to build absolute URLs (e.g. https://example.com)')

    def handle(self, *args, **options):
        trip_id = options.get('trip')
        date_str = options.get('date')
        dry_run = bool(options.get('dry_run'))
        send_emails = bool(options.get('send_emails'))
        domain = options.get('domain')

        try:
            cancel_date = datetime.date.fromisoformat(date_str)
        except Exception:
            raise CommandError('Invalid date format; use YYYY-MM-DD')

        try:
            trip = Trip.objects.get(pk=trip_id)
        except Trip.DoesNotExist:
            raise CommandError(f'Trip {trip_id} not found')

        tickets_qs = Ticket.objects.filter(trip=trip, travel_date=cancel_date, paid=True).select_related('user')
        tickets = list(tickets_qs)
        self.stdout.write(self.style.NOTICE(f'Found {len(tickets)} paid tickets for trip {trip_id} on {cancel_date}'))

        if dry_run:
            for t in tickets:
                self.stdout.write(f'  ticket {t.id} user={getattr(t.user, "email", None)} amount={getattr(t, "total_price", None)}')
            return

        # perform DB updates atomically
        with transaction.atomic():
            TripDayAvailability.objects.update_or_create(trip=trip, date=cancel_date, defaults={'available': False})

            for t in tickets:
                try:
                    refund_amount = float(getattr(t, 'total_price', 0) or 0)
                except Exception:
                    refund_amount = 0.0

                try:
                    Payment.objects.create(ticket=t, user=t.user, amount=-abs(refund_amount), currency=(t.currency or 'UAH'), status='refunded', provider='manual_refund', data={'by': 'management_command', 'ticket_id': t.id, 'reason': 'trip_cancelled'})
                except Exception:
                    logger.exception('Failed to create refund payment for ticket %s', getattr(t, 'id', None))

                try:
                    profile = getattr(getattr(t, 'user', None), 'profile', None)
                    if profile:
                        profile.balance = (profile.balance or 0) + refund_amount
                        profile.save(update_fields=['balance'])
                except Exception:
                    logger.exception('Failed to credit user balance for ticket %s', getattr(t, 'id', None))

                try:
                    t.paid = False
                    t.save(update_fields=['paid'])
                except Exception:
                    logger.exception('Failed to mark ticket unpaid %s', getattr(t, 'id', None))

                try:
                    if getattr(trip, 'carrier_user', None):
                        carrier_profile = getattr(trip.carrier_user, 'profile', None)
                        if carrier_profile:
                            carrier_profile.balance = (carrier_profile.balance or 0) - refund_amount
                            carrier_profile.save(update_fields=['balance'])
                            Payment.objects.create(ticket=None, user=trip.carrier_user, amount=-abs(refund_amount), currency=(t.currency or 'UAH'), status='refunded', provider='carrier_refund', data={'ticket_id': t.id, 'by': 'management_command', 'reason': 'trip_cancelled'})
                except Exception:
                    logger.exception('Failed to debit carrier for ticket %s', getattr(t, 'id', None))

        self.stdout.write(self.style.SUCCESS('Refunds applied and availability updated.'))

        if send_emails:
            if not domain:
                self.stdout.write(self.style.WARNING('No --domain provided; emails will be sent without absolute links.'))
            try:
                # import helper signature function (views provides _ticket_signature)
                from main.views import _ticket_signature
            except Exception:
                _ticket_signature = lambda ticket: ''

            sent = 0
            for t in tickets:
                try:
                    sig = _ticket_signature(t)
                    base = f"{domain.rstrip('/') if domain else ''}/ticket/cancellation/" if domain else ''
                    # build cancel/rebook/refund links similar to support_admin_cancel_trip
                    cancel_url = f"{domain.rstrip('/') if domain else ''}{'/cancellation/' + str(t.id) if domain else ''}?sig={sig}"
                    rebook_url = f"{domain.rstrip('/') if domain else ''}/kvitokindex?exchange_ticket={t.id}"
                    refund_url = f"{cancel_url}&action=refund"
                    subject = f"Скасовано рейс {t.from_city} → {t.to_city} — {cancel_date.strftime('%d.%m.%Y')}"
                    html = render_to_string('emails/cancellation_email.html', {'ticket': t, 'cancel_url': cancel_url, 'rebook_url': rebook_url, 'refund_url': refund_url, 'trip': trip, 'date': cancel_date})
                    msg = EmailMessage(subject, html, settings.DEFAULT_FROM_EMAIL, [t.contact_email or (t.user.email if getattr(t, 'user', None) else None)])
                    msg.content_subtype = 'html'
                    try:
                        res = msg.send(fail_silently=False)
                        if isinstance(res, int):
                            sent += res
                    except Exception:
                        logger.exception('Failed to send cancellation email for ticket %s', getattr(t, 'id', None))
                        continue
                except Exception:
                    logger.exception('Failed to build/send cancellation email for ticket %s', getattr(t, 'id', None))
                    continue

            self.stdout.write(self.style.SUCCESS(f'Emails attempted: {sent}'))
