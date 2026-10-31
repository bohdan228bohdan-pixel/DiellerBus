from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Send a test email using the configured email backend'

    def add_arguments(self, parser):
        parser.add_argument('recipient', help='Destination email address')

    def handle(self, *args, **options):
        recipient = options['recipient']
        self.stdout.write(f"Email backend: {settings.EMAIL_BACKEND}")
        self.stdout.write(f"SMTP credentials configured: {bool(settings.EMAIL_HOST_USER and settings.EMAIL_HOST_PASSWORD)}")

        try:
            sent = send_mail(
                'Test email from Dieller Bus',
                'This is a test email sent from Django.',
                settings.DEFAULT_FROM_EMAIL,
                [recipient],
                fail_silently=False,
            )
        except Exception as exc:
            raise CommandError(f'Email delivery failed: {exc}') from exc

        if sent != 1:
            raise CommandError('The email backend did not send the test message.')

        self.stdout.write(self.style.SUCCESS('Test email sent successfully.'))
