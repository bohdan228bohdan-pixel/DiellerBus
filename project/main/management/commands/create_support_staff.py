from django.core.management.base import BaseCommand
from django.contrib.auth.models import User


class Command(BaseCommand):
    help = 'Create three support staff accounts: support1/support2/support3 (password: supportpass)'

    def handle(self, *args, **options):
        users = [
            ('support1', 'support1@local', 'supportpass'),
            ('support2', 'support2@local', 'supportpass'),
            ('support3', 'support3@local', 'supportpass'),
        ]
        for username, email, password in users:
            if User.objects.filter(username=username).exists():
                self.stdout.write(self.style.WARNING(f"{username} already exists, skipping"))
                continue
            user = User.objects.create_user(username=username, email=email, password=password)
            user.is_staff = True
            user.save()
            self.stdout.write(self.style.SUCCESS(f"Created support user {username} / {password}"))
