from django.core.management.base import BaseCommand

from main.models import Trip

class Command(BaseCommand):
    help = 'Sync Trip.start_city and Trip.end_city from TripStop order'

    def handle(self, *args, **options):
        updated = 0
        for trip in Trip.objects.all():
            stops = list(trip.trip_stops.order_by('order').select_related('city'))
            if stops:
                start = stops[0].city
                end = stops[-1].city
                changed = False
                if trip.start_city_id != (start.id if start else None):
                    trip.start_city = start
                    changed = True
                if trip.end_city_id != (end.id if end else None):
                    trip.end_city = end
                    changed = True
                if changed:
                    trip.save(update_fields=['start_city', 'end_city'])
                    updated += 1
                    self.stdout.write(f'Updated trip {trip.id}: {start.name} -> {end.name}')
            else:
                if trip.start_city_id or trip.end_city_id:
                    trip.start_city = None
                    trip.end_city = None
                    trip.save(update_fields=['start_city', 'end_city'])
                    updated += 1
                    self.stdout.write(f'Cleared trip {trip.id}')
        self.stdout.write(self.style.SUCCESS(f'Done — updated {updated} trips'))
