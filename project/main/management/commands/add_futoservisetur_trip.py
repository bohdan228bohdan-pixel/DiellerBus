from datetime import date, time

from django.core.management.base import BaseCommand

from main.models import City, Route, RouteStop, Trip, TripStop, TripFare


class Command(BaseCommand):
    help = 'Add the Ternepil -> Warsaw route from Futoservisetur as a sellable trip'

    def handle(self, *args, **options):
        trip_date = date(2026, 7, 3)
        route_name = 'Тернопіль — Варшава'
        carrier_name = 'АВТОСЕРВІС ТУР ТОВ'

        cities_data = [
            ('Тернопіль', 'UA'),
            ('Кременець', 'UA'),
            ('Дубно', 'UA'),
            ('Млинів', 'UA'),
            ('Луцьк', 'UA'),
            ('Копачівка', 'UA'),
            ('Ковель', 'UA'),
            ('Вишнів', 'UA'),
            ('Ягодин', 'UA'),
            ('Холм', 'PL'),
            ('Люблін', 'PL'),
            ('Броновіце', 'PL'),
            ('Козеніце', 'PL'),
            ('Варка', 'PL'),
            ('Груєць', 'PL'),
            ('Аеропорт Фредеріка Шопена', 'PL'),
            ('Варшава', 'PL'),
        ]

        cities = {}
        for name, country in cities_data:
            city, created = City.objects.get_or_create(name=name, defaults={'country': country})
            cities[name] = city
            if created:
                self.stdout.write(f'Created city: {name}')

        route, created = Route.objects.get_or_create(
            name=route_name,
            defaults={
                'description': 'Маршрут Тернопіль — Варшава від АВТОСЕРВІС ТУР ТОВ',
                'active': True,
                'include_subcities': True,
            },
        )
        if created:
            self.stdout.write(f'Created route: {route_name}')

        route_stops = [
            ('Тернопіль', 'Автовокзал "Центральний"'),
            ('Кременець', 'Автостанція Кременець'),
            ('Дубно', 'Автостанція Дубно'),
            ('Млинів', 'Автостанція Млинів'),
            ('Луцьк', 'Автовокзал Луцьк'),
            ('Копачівка', 'Зупинка Копачівка'),
            ('Ковель', 'Зупинка Ковель (біля 7 школи)'),
            ('Вишнів', 'Зупинка Вишнів'),
            ('Ягодин', 'Зупинка "КПП"'),
            ('Холм', 'Зупинка "МакДональдс"'),
            ('Люблін', 'Автовокзал Люблін (старий автовокзал)'),
            ('Броновіце', 'Зупинка Броновіце'),
            ('Козеніце', 'Автовокзал Козеніце'),
            ('Варка', 'Зупинка Варка (стара Biedronka)'),
            ('Груєць', 'Зупинка "Пошта"'),
            ('Аеропорт Фредеріка Шопена', 'Зупинка Аеропорт'),
            ('Варшава', 'Автовокзал "Варшава-Заходня"'),
        ]

        for order, (city_name, address) in enumerate(route_stops, start=1):
            city_obj = cities[city_name]
            RouteStop.objects.update_or_create(
                route=route,
                city=city_obj,
                defaults={'order': order, 'address': address},
            )

        trip, created = Trip.objects.get_or_create(
            route=route,
            title='Рейс: Тернопіль — Варшава',
            date=trip_date,
            defaults={
                'carrier': carrier_name,
                'seats': 50,
                'base_price': 2070.00,
                'currency': 'UAH',
                'direction': 'UA_PL',
                'is_international': True,
                'active': True,
            },
        )
        if created:
            self.stdout.write('Created trip: Тернопіль — Варшава')
        else:
            trip.carrier = carrier_name
            trip.seats = 50
            trip.base_price = 2070.00
            trip.currency = 'UAH'
            trip.direction = 'UA_PL'
            trip.is_international = True
            trip.active = True
            trip.save()

        stop_data = [
            ('Тернопіль', time(14, 15), None, 0.00),
            ('Кременець', time(15, 25), None, 0.00),
            ('Дубно', time(16, 40), None, 0.00),
            ('Млинів', time(17, 0), None, 0.00),
            ('Луцьк', time(18, 0), time(18, 15), 0.00),
            ('Копачівка', None, time(18, 30), 0.00),
            ('Ковель', None, time(19, 50), 0.00),
            ('Вишнів', None, time(20, 15), 0.00),
            ('Ягодин', None, time(20, 45), 0.00),
            ('Холм', None, time(21, 55), 0.00),
            ('Люблін', None, time(22, 40), 0.00),
            ('Броновіце', None, time(23, 25), 0.00),
            ('Козеніце', None, time(23, 45), 0.00),
            ('Варка', None, time(1, 35), 0.00),
            ('Груєць', None, time(1, 45), 0.00),
            ('Аеропорт Фредеріка Шопена', None, time(2, 40), 0.00),
            ('Варшава', time(2, 50), None, 0.00),
        ]

        for order, (city_name, arrival_time, departure_time, price) in enumerate(stop_data, start=1):
            city_obj = cities[city_name]
            TripStop.objects.update_or_create(
                trip=trip,
                city=city_obj,
                defaults={
                    'order': order,
                    'arrival_time': arrival_time,
                    'departure_time': departure_time,
                    'price': price,
                    'address': next((a for c, a in route_stops if c == city_name), ''),
                },
            )

        start_city = cities['Тернопіль']
        end_city = cities['Варшава']
        TripFare.objects.update_or_create(
            trip=trip,
            from_city=start_city,
            to_city=end_city,
            defaults={'price': 2070.00, 'currency': 'UAH'},
        )

        self.stdout.write(self.style.SUCCESS(f'Added sellable trip: {route_name} on {trip_date.isoformat()}'))
