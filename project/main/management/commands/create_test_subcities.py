from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = 'Create a test route with subcities (Луцьк + Копачівка -> Варшава)'

    def handle(self, *args, **options):
        from main.models import City, Route, RouteStop, Trip, TripStop, TripFare
        today = timezone.now().date()

        # create main cities
        lutsk, _ = City.objects.get_or_create(name='Луцьк', defaults={'country': 'UA'})
        kopachivka, _ = City.objects.get_or_create(name='Копачівка', defaults={'country': 'UA', 'parent': lutsk})
        # ensure parent set
        if kopachivka.parent_id != lutsk.id:
            kopachivka.parent = lutsk
            kopachivka.save()

        warsaw, _ = City.objects.get_or_create(name='Варшава', defaults={'country': 'PL'})

        # create route
        route, created = Route.objects.get_or_create(name='Тест: Луцьк — Варшава', defaults={'description': 'Тестовий маршрут з підмістами', 'active': True, 'include_subcities': True})
        if not created:
            route.include_subcities = True
            route.active = True
            route.save()

        # create route stops
        RouteStop.objects.get_or_create(route=route, city=lutsk, defaults={'order': 1, 'address': 'Луцьк (центр)'})
        RouteStop.objects.get_or_create(route=route, city=warsaw, defaults={'order': 2, 'address': 'Warszawa'} )

        # create a sample trip
        trip, tc = Trip.objects.get_or_create(route=route, date=today + timezone.timedelta(days=1), defaults={'title': 'Тестовий рейс', 'seats': 50, 'base_price': 1500.00, 'currency': 'UAH', 'active': True})

        # create trip stops with per-leg price
        TripStop.objects.get_or_create(trip=trip, city=lutsk, defaults={'order': 1, 'price': 0.0, 'address': 'Луцьк'})
        TripStop.objects.get_or_create(trip=trip, city=warsaw, defaults={'order': 2, 'price': 0.0, 'address': 'Варшава'})

        # create TripFare for main cities
        fare, fcreated = TripFare.objects.get_or_create(trip=trip, from_city=lutsk, to_city=warsaw, defaults={'price': 1500.00, 'currency': 'UAH'})

        self.stdout.write(self.style.SUCCESS('Created test route and data:'))
        self.stdout.write(f'  Cities: {lutsk} (id={lutsk.id}), {kopachivka} (id={kopachivka.id}, parent={kopachivka.parent_id}), {warsaw} (id={warsaw.id})')
        self.stdout.write(f'  Route: {route} (include_subcities={route.include_subcities})')
        self.stdout.write(f'  Trip: {trip} (id={trip.id}), Fare: {fare} (price={fare.price})')
