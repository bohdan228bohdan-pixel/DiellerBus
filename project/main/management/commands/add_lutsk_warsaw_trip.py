from datetime import date, time

from django.core.management.base import BaseCommand

from main.models import City, Route, RouteStop, Trip, TripStop, TripFare


class Command(BaseCommand):
    help = 'Add a direct Lutsk -> Warsaw trip with 15% markup applied to the listed 1300 UAH price'

    def handle(self, *args, **options):
        trip_date = date(2026, 7, 3)
        carrier_name = 'АВТОСЕРВІС ТУР ТОВ'
        route_name = 'Луцьк — Варшава'

        route, created = Route.objects.get_or_create(
            name=route_name,
            defaults={'description': 'Прямий рейс Луцьк — Варшава від АВТОСЕРВІС ТУР ТОВ', 'active': True, 'include_subcities': True},
        )
        if created:
            self.stdout.write(f'Created route: {route_name}')

        lutsk = City.objects.get_or_create(name='Луцьк', defaults={'country': 'UA'})[0]
        warsaw = City.objects.get_or_create(name='Варшава', defaults={'country': 'PL'})[0]

        route_stops = [
            (lutsk, 'Зупинка "АЗС БРСМ"'),
            (lutsk, 'Автовокзал Луцьк'),
            (City.objects.get_or_create(name='Копачівка', defaults={'country': 'UA'})[0], 'Зупинка Копачівка'),
            (City.objects.get_or_create(name='Голоби', defaults={'country': 'UA'})[0], 'Зупинка Голоби'),
            (City.objects.get_or_create(name='Колодяжне', defaults={'country': 'UA'})[0], 'Зупинка Колодяжне'),
            (City.objects.get_or_create(name='Ковель', defaults={'country': 'UA'})[0], 'Автостанція "Ковель"'),
            (City.objects.get_or_create(name='Ковель', defaults={'country': 'UA'})[0], 'Зупинка "АЗС UPG"'),
            (City.objects.get_or_create(name='Луків', defaults={'country': 'UA'})[0], 'Зупинка Луков'),
            (City.objects.get_or_create(name='Вишнів', defaults={'country': 'UA'})[0], 'Зупинка Вишнів'),
            (City.objects.get_or_create(name='Ягодин', defaults={'country': 'UA'})[0], 'Зупинка "КПП"'),
            (City.objects.get_or_create(name='Холм', defaults={'country': 'PL'})[0], 'Зупинка "МакДональдс"'),
            (City.objects.get_or_create(name='Люблін', defaults={'country': 'PL'})[0], 'Автовокзал Люблін (старий автовокзал)'),
            (City.objects.get_or_create(name='Пулави', defaults={'country': 'PL'})[0], 'Автостанція Пулави'),
            (City.objects.get_or_create(name='Козеніце', defaults={'country': 'PL'})[0], 'Автовокзал Козеніце'),
            (City.objects.get_or_create(name='Варка', defaults={'country': 'PL'})[0], 'Зупинка Варка'),
            (City.objects.get_or_create(name='Груєць', defaults={'country': 'PL'})[0], 'Зупинка "Пошта"'),
            (City.objects.get_or_create(name='Аеропорт Фредеріка Шопена', defaults={'country': 'PL'})[0], 'Автобусний термінал "Аеропорт Шопена" (Окенче)'),
            (warsaw, 'Автовокзал "Варшава-Заходня"'),
        ]

        for order, (city_obj, address) in enumerate(route_stops, start=1):
            RouteStop.objects.update_or_create(
                route=route,
                city=city_obj,
                order=order,
                defaults={'address': address},
            )

        price = round(1300.00 * 1.15, 2)
        trip, created = Trip.objects.get_or_create(
            route=route,
            title='Рейс: Луцьк — Варшава',
            date=trip_date,
            defaults={
                'carrier': carrier_name,
                'seats': 50,
                'base_price': price,
                'currency': 'UAH',
                'direction': 'UA_PL',
                'is_international': True,
                'active': True,
            },
        )
        if created:
            self.stdout.write(f'Created trip: {trip.title}')
        else:
            trip.carrier = carrier_name
            trip.seats = 50
            trip.base_price = price
            trip.currency = 'UAH'
            trip.direction = 'UA_PL'
            trip.is_international = True
            trip.active = True
            trip.save()

        stop_times = [
            (time(6, 10), None),
            (time(6, 40), None),
            (time(7, 0), None),
            (time(7, 20), None),
            (time(7, 35), None),
            (time(8, 5), None),
            (time(8, 15), None),
            (time(8, 20), None),
            (time(8, 45), None),
            (time(9, 0), None),
            (time(10, 10), None),
            (time(11, 15), None),
            (time(12, 10), None),
            (time(12, 50), None),
            (time(14, 0), None),
            (time(14, 40), None),
            (time(15, 10), None),
            (None, time(15, 30)),
        ]

        for order, ((city_obj, address), (arrival, departure)) in enumerate(zip(route_stops, stop_times), start=1):
            TripStop.objects.update_or_create(
                trip=trip,
                city=city_obj,
                order=order,
                defaults={
                    'address': address,
                    'arrival_time': arrival,
                    'departure_time': departure,
                    'price': 0.00,
                },
            )

        TripFare.objects.update_or_create(
            trip=trip,
            from_city=lutsk,
            to_city=warsaw,
            defaults={'price': price, 'currency': 'UAH'},
        )

        self.stdout.write(self.style.SUCCESS(f'Added/updated route {route_name} with price {price} UAH'))
