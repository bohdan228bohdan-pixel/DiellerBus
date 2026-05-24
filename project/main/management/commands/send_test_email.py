from django.core.management.base import BaseCommand
from django.core.mail import send_mail, EmailMessage
from django.conf import settings


class Command(BaseCommand):
    help = (
        "Send a test email using current EMAIL_* settings.\n"
        "Usage: python manage.py send_test_email recipient@example.com --subject 'Hi' --message 'Hello'"
    )

    def add_arguments(self, parser):
        parser.add_argument('recipient', nargs=1, help='Recipient email address')
        parser.add_argument('--subject', default='Test email from Dieller Bus', help='Email subject')
        parser.add_argument('--message', default='This is a test email sent from Django.', help='Message body')
        parser.add_argument('--html', action='store_true', help='Send message as HTML')
        parser.add_argument('--from-email', dest='from_email', default=None, help='Override FROM email address')

    def handle(self, *args, **options):
        recipient = options['recipient'][0]
        subject = options['subject']
        message = options['message']
        from_email = options['from_email'] or getattr(settings, 'DEFAULT_FROM_EMAIL', None)
        send_as_html = options['html']

        self.stdout.write(f"Using EMAIL_BACKEND={getattr(settings, 'EMAIL_BACKEND', '')}")
        self.stdout.write(f"Sending test email to: {recipient}")

        try:
            if send_as_html:
                email = EmailMessage(subject, message, from_email, [recipient])
                email.content_subtype = 'html'
                email.send(fail_silently=False)
            else:
                # send_mail returns number of emails sent
                send_mail(subject, message, from_email, [recipient], fail_silently=False)
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"Failed to send test email: {exc}"))
            return

        self.stdout.write(self.style.SUCCESS(f"Test email sent to {recipient}"))
