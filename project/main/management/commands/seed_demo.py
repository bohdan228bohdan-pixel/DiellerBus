from django.core.management.base import BaseCommand
from datetime import date, time

from main.models import City, Route, RouteStop, Trip, TripStop


class Command(BaseCommand):
    help = 'Seed demo cities, route, and trips for local testing'

    def handle(self, *args, **options):
        cities_data = [
            ('Луцьк', 'UA'),
            ('Ковель', 'UA'),
            ('Ягодин', 'UA'),
            ('Любомль', 'UA'),
            ('Люблін', 'PL'),
            ('Пулави', 'PL'),
            ('Хелм', 'PL'),
        ]

        cities = {}
        for name, country in cities_data:
            obj, created = City.objects.get_or_create(name=name, defaults={'country': country})
            cities[name] = obj
            if created:
                self.stdout.write(f"Created city: {name}")

        route, created = Route.objects.get_or_create(
            name='Луцьк — Пулави',
            defaults={'description': 'Demo route from Луцьк to Пулави', 'active': True}
        )
        if created:
            self.stdout.write('Created route: Луцьк — Пулави')

        route_stops = [
            ('Луцьк', 'Автовокзал Луцьк, вул. Конякіна, 39'),
            ('Ковель', 'Автостанція Ковель'),
            ('Любомль', 'Зупинка Любомль (Вишнів)'),
            ('Ягодин', 'КПП "Ягодин"'),
            ('Хелм', 'Зупинка Холм'),
            ('Люблін', 'Автовокзал Люблін'),
            ('Пулави', 'Автостанція Пулави'),
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
            title='Ранковий рейс',
            date=trip_date,
            defaults={'base_price': 800.0, 'currency': 'UAH', 'seats': 30, 'direction': 'UA_PL', 'discount_percent': 10}
        )
        if created:
            self.stdout.write('Created trip: Ранковий рейс')

        # approximate times for each stop
        times = [
            (time(8, 0), time(8, 5)),
            (time(10, 0), time(10, 10)),
            (time(11, 30), time(11, 35)),
            (time(12, 30), time(12, 35)),
            (time(13, 30), time(13, 35)),
            (time(15, 0), time(15, 10)),
            (time(17, 30), time(17, 35)),
        ]

        for idx, ((city_name, addr), (arr, dep)) in enumerate(zip(route_stops, times), start=1):
            city_obj = cities[city_name]
            obj, created = TripStop.objects.update_or_create(
                trip=trip, city=city_obj, order=idx,
                defaults={'arrival_time': arr, 'departure_time': dep, 'address': addr}
            )
            if created:
                self.stdout.write(f'Added trip stop: {city_name}')

        self.stdout.write(self.style.SUCCESS('Demo data seeded'))
