from datetime import date, time

from django.core.management.base import BaseCommand

from main.models import City, Route, RouteStop, Trip, TripStop, TripFare


class Command(BaseCommand):
    help = 'Add АВТОСЕРВІС ТУР ТОВ Warsaw->Ternopil and Warsaw->Lutsk trips for 4 July 2026'

    def handle(self, *args, **options):
        trip_date = date(2026, 7, 4)
        carrier_name = 'АВТОСЕРВІС ТУР ТОВ'

        cities_data = [
            ('Варшава', 'PL'),
            ('Аеропорт Фредеріка Шопена', 'PL'),
            ('Груєць', 'PL'),
            ('Варка', 'PL'),
            ('Козеніце', 'PL'),
            ('Броновіце', 'PL'),
            ('Пулави', 'PL'),
            ('Люблін', 'PL'),
            ('Холм', 'PL'),
            ('Ягодин', 'UA'),
            ('Вишнів', 'UA'),
            ('Ковель', 'UA'),
            ('Голоби', 'UA'),
            ('Копачівка', 'UA'),
            ('Луцьк', 'UA'),
            ('Млинів', 'UA'),
            ('Дубно', 'UA'),
            ('Кременець', 'UA'),
            ('Тернопіль', 'UA'),
        ]

        cities = {}
        for name, country in cities_data:
            city, created = City.objects.get_or_create(name=name, defaults={'country': country})
            cities[name] = city
            if created:
                self.stdout.write(f'Created city: {name}')

        self.create_warsaw_ternopil_trip(trip_date, carrier_name, cities)
        self.create_warsaw_lutsk_trip(trip_date, carrier_name, cities)

    def create_warsaw_ternopil_trip(self, trip_date, carrier_name, cities):
        route_name = 'Варшава — Тернопіль'
        route, created = Route.objects.get_or_create(
            name=route_name,
            defaults={
                'description': 'Прямий рейс Варшава — Тернопіль від АВТОСЕРВІС ТУР ТОВ',
                'active': True,
                'include_subcities': True,
            },
        )
        if created:
            self.stdout.write(f'Created route: {route_name}')

        route_stops = [
            ('Варшава', 'Автовокзал "Варшава-Заходня"'),
            ('Аеропорт Фредеріка Шопена', 'Аеропорт ім. Фредеріка Шопена (автобусний термінал)'),
            ('Груєць', 'Зупинка "Торгівельний центр Kaufland"'),
            ('Груєць', 'Зупинка "Пошта"'),
            ('Варка', 'Зупинка Варка (стара Biedronka)'),
            ('Козеніце', 'Автовокзал Козеніце'),
            ('Броновіце', 'Зупинка Броновіце'),
            ('Пулави', 'Автостанція Пулави'),
            ('Люблін', 'Автовокзал Люблін (старий автовокзал)'),
            ('Холм', 'Зупинка Холм (за синім з/д мостом, біля магазину МЕРЕ)'),
            ('Ягодин', 'Зупинка "Старовойтове"'),
            ('Вишнів', 'Зупинка Вишнів'),
            ('Ковель', 'Автостанція "Ковель"'),
            ('Голоби', 'Зупинка Голоби'),
            ('Копачівка', 'Зупинка Копачівка'),
            ('Луцьк', 'Автовокзал Луцьк'),
            ('Млинів', 'Автостанція Млинів'),
            ('Дубно', 'Автостанція Дубно'),
            ('Кременець', 'Автостанція Кременець'),
            ('Тернопіль', 'Автовокзал "Центральний"'),
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
            title='Рейс: Варшава — Тернопіль',
            date=trip_date,
            defaults={
                'carrier': carrier_name,
                'seats': 50,
                'base_price': 1800.00,
                'currency': 'UAH',
                'direction': 'PL_UA',
                'is_international': True,
                'active': True,
            },
        )
        if created:
            self.stdout.write('Created trip: Варшава — Тернопіль')
        else:
            trip.carrier = carrier_name
            trip.seats = 50
            trip.base_price = 1800.00
            trip.currency = 'UAH'
            trip.direction = 'PL_UA'
            trip.is_international = True
            trip.active = True
            trip.save()

        stop_data = [
            ('Варшава', None, time(19, 14), 0.00),
            ('Аеропорт Фредеріка Шопена', None, time(19, 34), 0.00),
            ('Груєць', None, time(19, 51), 0.00),
            ('Груєць', None, time(19, 59), 0.00),
            ('Варка', None, time(20, 29), 0.00),
            ('Козеніце', None, time(20, 54), 0.00),
            ('Броновіце', None, time(21, 14), 0.00),
            ('Пулави', None, time(21, 24), 0.00),
            ('Люблін', None, time(22, 30), 0.00),
            ('Холм', None, time(23, 34), 0.00),
            ('Ягодин', None, time(1, 10), 0.00),
            ('Вишнів', None, time(3, 20), 0.00),
            ('Ковель', None, time(4, 20), 0.00),
            ('Голоби', None, time(4, 35), 0.00),
            ('Копачівка', None, time(4, 50), 0.00),
            ('Луцьк', None, time(5, 40), 0.00),
            ('Млинів', None, time(6, 5), 0.00),
            ('Дубно', None, time(7, 5), 0.00),
            ('Кременець', None, time(8, 5), 0.00),
            ('Тернопіль', time(9, 5), None, 0.00),
        ]

        for order, (city_name, arrival_time, departure_time, price) in enumerate(stop_data, start=1):
            city_obj = cities[city_name]
            address = next((addr for name, addr in route_stops if name == city_name), '')
            TripStop.objects.update_or_create(
                trip=trip,
                city=city_obj,
                order=order,
                defaults={
                    'arrival_time': arrival_time,
                    'departure_time': departure_time,
                    'price': price,
                    'address': address,
                },
            )

        TripFare.objects.update_or_create(
            trip=trip,
            from_city=cities['Варшава'],
            to_city=cities['Тернопіль'],
            defaults={'price': 1800.00, 'currency': 'UAH'},
        )
        self.stdout.write(self.style.SUCCESS('Added/updated trip: Варшава — Тернопіль'))

    def create_warsaw_lutsk_trip(self, trip_date, carrier_name, cities):
        route_name = 'Варшава — Луцьк'
        route, created = Route.objects.get_or_create(
            name=route_name,
            defaults={
                'description': 'Прямий рейс Варшава — Луцьк від АВТОСЕРВІС ТУР ТОВ',
                'active': True,
                'include_subcities': True,
            },
        )
        if created:
            self.stdout.write(f'Created route: {route_name}')

        route_stops = [
            ('Варшава', 'Автовокзал "Варшава-Заходня"'),
            ('Аеропорт Фредеріка Шопена', 'Зупинка "Аеропорт ім. Фредеріка Шопена, 1-й рівень зони приліту (вихід з 1 сектору до автовокзалу)"'),
            ('Груєць', 'Зупинка "Пошта"'),
            ('Варка', 'Зупинка Варка (стара Biedronka)'),
            ('Козеніце', 'Автовокзал Козеніце'),
            ('Броновіце', 'Зупинка Броновіце'),
            ('Пулави', 'Автостанція Пулави'),
            ('Люблін', 'Автовокзал Люблін (старий автовокзал)'),
            ('Холм', 'Зупинка "МакДональдс"'),
            ('Ягодин', 'Зупинка "Старовойтове"'),
            ('Вишнів', 'Зупинка Вишнів'),
            ('Ковель', 'Автостанція "Ковель"'),
            ('Луцьк', 'Автовокзал Луцьк'),
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
            title='Рейс: Варшава — Луцьк',
            date=trip_date,
            defaults={
                'carrier': carrier_name,
                'seats': 50,
                'base_price': 1650.00,
                'currency': 'UAH',
                'direction': 'PL_UA',
                'is_international': True,
                'active': True,
            },
        )
        if created:
            self.stdout.write('Created trip: Варшава — Луцьк')
        else:
            trip.carrier = carrier_name
            trip.seats = 50
            trip.base_price = 1650.00
            trip.currency = 'UAH'
            trip.direction = 'PL_UA'
            trip.is_international = True
            trip.active = True
            trip.save()

        stop_data = [
            ('Варшава', None, time(6, 10), 0.00),
            ('Аеропорт Фредеріка Шопена', None, time(6, 25), 0.00),
            ('Груєць', None, time(6, 45), 0.00),
            ('Варка', None, time(7, 30), 0.00),
            ('Козеніце', None, time(8, 0), 0.00),
            ('Броновіце', None, time(8, 25), 0.00),
            ('Пулави', None, time(9, 10), 0.00),
            ('Люблін', None, time(9, 45), 0.00),
            ('Холм', None, time(10, 50), 0.00),
            ('Ягодин', None, time(13, 30), 0.00),
            ('Вишнів', None, time(13, 50), 0.00),
            ('Ковель', None, time(15, 10), 0.00),
            ('Луцьк', time(16, 15), None, 0.00),
        ]

        for order, (city_name, arrival_time, departure_time, price) in enumerate(stop_data, start=1):
            city_obj = cities[city_name]
            address = next((addr for name, addr in route_stops if name == city_name), '')
            TripStop.objects.update_or_create(
                trip=trip,
                city=city_obj,
                order=order,
                defaults={
                    'arrival_time': arrival_time,
                    'departure_time': departure_time,
                    'price': price,
                    'address': address,
                },
            )

        TripFare.objects.update_or_create(
            trip=trip,
            from_city=cities['Варшава'],
            to_city=cities['Луцьк'],
            defaults={'price': 1650.00, 'currency': 'UAH'},
        )
        self.stdout.write(self.style.SUCCESS('Added/updated trip: Варшава — Луцьк'))
