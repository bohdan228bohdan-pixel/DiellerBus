from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from main.models import Trip, TripDayAvailability


class Command(BaseCommand):
    help = 'Ensure TripDayAvailability entries exist for the next N days and remove past entries.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=31, help='How many days ahead to ensure (default: 31)')

    def handle(self, *args, **options):
        days = options.get('days') or 31
        today = timezone.localdate()
        end_date = today + timedelta(days=days - 1)

        # Delete old availability records (before today)
        old_qs = TripDayAvailability.objects.filter(date__lt=today)
        old_count = old_qs.count()
        if old_count:
            old_qs.delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {old_count} TripDayAvailability records older than {today.isoformat()}"))

        added = 0
        trips = Trip.objects.filter(active=True)
        for trip in trips:
            for i in range(days):
                d = today + timedelta(days=i)
                obj, created = TripDayAvailability.objects.get_or_create(trip=trip, date=d, defaults={'available': True})
                if created:
                    added += 1

        self.stdout.write(self.style.SUCCESS(f"Ensured availability up to {end_date.isoformat()} — added {added} new records."))
