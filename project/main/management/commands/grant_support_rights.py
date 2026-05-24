from django.core.management.base import BaseCommand
from django.contrib.auth.models import User

from main.models import SupportWorker


class Command(BaseCommand):
    help = 'Grant support worker rights to an existing user (creates SupportWorker and sets is_staff)'

    def add_arguments(self, parser):
        parser.add_argument('username', nargs='?', help='Username to grant support rights to', default='dieller')

    def handle(self, *args, **options):
        username = options.get('username')
        if not username:
            self.stdout.write(self.style.ERROR('Username required'))
            return
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'User {username} not found'))
            return

        user.is_staff = True
        user.save()

        if hasattr(user, 'support_worker') and user.support_worker:
            self.stdout.write(self.style.WARNING(f'User {username} already has SupportWorker'))
            return

        sw = SupportWorker.objects.create(user=user, email=user.email, full_name=(user.get_full_name() or user.username))
        sw.save()
        self.stdout.write(self.style.SUCCESS(f'Granted support rights to {username} (SupportWorker id={sw.id})'))
