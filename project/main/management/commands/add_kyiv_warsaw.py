from django.core.management.base import BaseCommand
from datetime import date, time

from main.models import City, Route, RouteStop, Trip, TripStop


class Command(BaseCommand):
    help = 'Add Kyiv->Warsaw trip with Lutsk stop and per-stop prices'

    def handle(self, *args, **options):
        cities_data = [
            ('Київ', 'UA'),
            ('Луцьк', 'UA'),
            ('Варшава', 'PL'),
        ]

        cities = {}
        for name, country in cities_data:
            obj, created = City.objects.get_or_create(name=name, defaults={'country': country})
            cities[name] = obj
            if created:
                self.stdout.write(f'Created city: {name}')

        route, created = Route.objects.get_or_create(
            name='Київ — Варшава',
            defaults={'description': 'Route Київ — Варшава', 'active': True}
        )
        if created:
            self.stdout.write('Created route: Київ — Варшава')

        route_stops = [
            ('Київ', 'Автовокзал Київ'),
            ('Луцьк', 'Автовокзал Луцьк, вул. Конякіна, 39'),
            ('Варшава', 'Автовокзал Варшава'),
        ]

        for i, (city_name, addr) in enumerate(route_stops, start=1):
            city_obj = cities[city_name]
            obj, created = RouteStop.objects.update_or_create(
                route=route, city=city_obj, order=i,
                defaults={'address': addr}
            )
            if created:
                self.stdout.write(f'Added route stop: {city_name}')

        trip_date = date.today()
        trip, created = Trip.objects.get_or_create(
            route=route,
            title='Рейс: Київ — Варшава',
            date=trip_date,
            defaults={'base_price': 1400.0, 'currency': 'UAH', 'seats': 40, 'direction': 'UA_PL', 'discount_percent': 0}
        )
        if created:
            self.stdout.write('Created trip: Рейс: Київ — Варшава')

        # Define per-stop times and prices
        # Kyiv depart 12:00, price for Kyiv->Lutsk leg = 300
        # Lutsk arrive 16:00 depart 16:15, price for Lutsk->Warsaw leg = 1100
        # Warsaw arrive 04:00
        stops_info = [
            ('Київ', None, time(12, 0), 300.0),
            ('Луцьк', time(16, 0), time(16, 15), 1100.0),
            ('Варшава', time(4, 0), None, 0.0),
        ]

        for idx, (city_name, arr, dep, price) in enumerate(stops_info, start=1):
            city_obj = cities[city_name]
            addr = next((a for (c, a) in route_stops if c == city_name), '')
            defaults = {'arrival_time': arr, 'departure_time': dep, 'price': price, 'address': addr}
            obj, created = TripStop.objects.update_or_create(
                trip=trip, city=city_obj, order=idx,
                defaults=defaults
            )
            if created:
                self.stdout.write(f'Added trip stop: {city_name} (price={price})')
            else:
                self.stdout.write(f'Updated trip stop: {city_name} (price={price})')

        self.stdout.write(self.style.SUCCESS('Kyiv->Warsaw trip created/updated successfully'))
