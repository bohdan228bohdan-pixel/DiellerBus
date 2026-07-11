from datetime import date, datetime, time

from django.core.management.base import BaseCommand

from main.models import City, Route, RouteStop, Trip, TripStop, TripFare


class Command(BaseCommand):
    help = 'Add Mihalyuk carrier routes as sellable trips with 15% markup'

    def handle(self, *args, **options):
        trip_date = date(2026, 7, 3)
        carrier_name = 'Михалюк Ю.П'

        routes = [
            {
                'route_name': 'Луцьк — Львів',
                'start_city': 'Луцьк',
                'end_city': 'Львів',
                'direction': 'UA_UA',
                'carrier': carrier_name,
                'trips': [
                    {
                        'title': 'Луцьк — Львів 05:40',
                        'depart': time(5, 40),
                        'arrive': time(9, 40),
                        'price': 506.00,
                        'stops': [
                            ('АС-2', 'АС-2'),
                            ('Коршів пов.', 'Коршів пов.'),
                            ('Чаруків', 'Чаруків'),
                            ('Городище', 'Городище'),
                            ('Угринів пов.', 'Угринів пов.'),
                            ('Шклінь', 'Шклінь'),
                            ('Софіївка', 'Софіївка'),
                            ('Ярівка', 'Ярівка'),
                            ('Горохів', 'Горохів'),
                            ('Сільце', 'Сільце'),
                            ('Журавники', 'Журавники'),
                            ('Стоянів', 'Стоянів'),
                            ('Радехів', 'Радехів'),
                            ('Кам\'янка-Бузька', 'Кам\'янка-Бузька'),
                            ('Львів', 'Львів'),
                        ],
                    },
                    {
                        'title': 'Луцьк — Львів 06:50',
                        'depart': time(6, 50),
                        'arrive': time(12, 20),
                        'price': 544.00,
                        'stops': [
                            ('Торчин', 'Торчин'),
                            ('Затурці', 'Затурці'),
                            ('Локачі', 'Локачі'),
                            ('Павлівка', 'Павлівка'),
                            ('Іваничі', 'Іваничі'),
                            ('Сокаль', 'Сокаль'),
                            ('Шептицький', 'Шептицький'),
                            ('Великі Мости', 'Великі Мости'),
                            ('Жовква', 'Жовква'),
                            ('Львів', 'Львів'),
                        ],
                    },
                    {
                        'title': 'Луцьк — Львів 10:50',
                        'depart': time(10, 50),
                        'arrive': time(14, 45),
                        'price': 506.00,
                        'stops': [
                            ('Луцьк АС-2', 'Луцьк АС-2'),
                            ('Чаруків', 'Чаруків'),
                            ('Городище', 'Городище'),
                            ('Угринів пов.', 'Угринів пов.'),
                            ('Шклінь', 'Шклінь'),
                            ('Софіївка', 'Софіївка'),
                            ('Терешківці', 'Терешківці'),
                            ('Горохів', 'Горохів'),
                            ('Сільце', 'Сільце'),
                            ('Журавники', 'Журавники'),
                            ('Стоянів', 'Стоянів'),
                            ('Радехів', 'Радехів'),
                            ('Кам\'янка-Бузька', 'Кам\'янка-Бузька'),
                            ('Львів', 'Львів'),
                        ],
                    },
                    {
                        'title': 'Луцьк — Львів 15:50',
                        'depart': time(15, 50),
                        'arrive': time(20, 5),
                        'price': 506.00,
                        'stops': [
                            ('Луцьк АС-2', 'Луцьк АС-2'),
                            ('Чаруків', 'Чаруків'),
                            ('Городище', 'Городище'),
                            ('Угринів', 'Угринів'),
                            ('Шклінь', 'Шклінь'),
                            ('Софіївка', 'Софіївка'),
                            ('Терешківці', 'Терешківці'),
                            ('Горохів', 'Горохів'),
                            ('Сільце', 'Сільце'),
                            ('Журавники', 'Журавники'),
                            ('Стоянів', 'Стоянів'),
                            ('Радехів', 'Радехів'),
                            ('Кам\'янка-Бузька', 'Кам\'янка-Бузька'),
                            ('Львів', 'Львів'),
                        ],
                    },
                ],
            },
            {
                'route_name': 'Л. Пісня — Рівне',
                'start_city': 'Л. Пісня',
                'end_city': 'Рівне',
                'direction': 'UA_UA',
                'carrier': carrier_name,
                'trips': [
                    {
                        'title': 'Л. Пісня — Рівне 10:55',
                        'depart': time(10, 55),
                        'arrive': time(12, 40),
                        'price': 316.00,
                        'stops': [
                            ('Дерно', 'Дерно'),
                            ('Клевань', 'Клевань'),
                            ('Зоря', 'Зоря'),
                            ('Рівне', 'Рівне'),
                        ],
                    }
                ],
            },
            {
                'route_name': 'Луцьк — Тернопіль',
                'start_city': 'Луцьк',
                'end_city': 'Тернопіль',
                'direction': 'UA_UA',
                'carrier': carrier_name,
                'trips': [
                    {
                        'title': 'Луцьк — Тернопіль 17:25',
                        'depart': time(17, 25),
                        'arrive': time(21, 20),
                        'price': 506.00,
                        'stops': [
                            ('Дубно', 'Дубно'),
                            ('Кременець', 'Кременець'),
                            ('Вишневець', 'Вишневець'),
                            ('Тернопіль', 'Тернопіль'),
                        ],
                    }
                ],
            },
        ]

        for route_data in routes:
            route, _ = Route.objects.get_or_create(
                name=route_data['route_name'],
                defaults={'description': f'Маршрут {route_data["route_name"]} від {carrier_name}', 'active': True, 'include_subcities': True},
            )

            start_city = self._get_or_create_city(route_data['start_city'])
            end_city = self._get_or_create_city(route_data['end_city'])
            RouteStop.objects.update_or_create(route=route, city=start_city, defaults={'order': 1, 'address': start_city.name})
            RouteStop.objects.update_or_create(route=route, city=end_city, defaults={'order': 2, 'address': end_city.name})

            for trip_data in route_data['trips']:
                trip, created = Trip.objects.get_or_create(
                    route=route,
                    title=trip_data['title'],
                    date=trip_date,
                    defaults={
                        'carrier': route_data['carrier'],
                        'seats': 50,
                        'base_price': trip_data['price'],
                        'currency': 'UAH',
                        'direction': route_data['direction'],
                        'is_international': False,
                        'active': True,
                    },
                )
                if not created:
                    trip.carrier = route_data['carrier']
                    trip.seats = 50
                    trip.base_price = trip_data['price']
                    trip.currency = 'UAH'
                    trip.direction = route_data['direction']
                    trip.is_international = False
                    trip.active = True
                    trip.save()

                stop_names = []
                for order, (stop_name, address) in enumerate(trip_data['stops'], start=1):
                    stop_city = self._get_or_create_city(stop_name)
                    stop_names.append(stop_name)
                    defaults = {
                        'order': order,
                        'address': address,
                    }
                    if order == 1 and trip_data.get('depart'):
                        defaults['departure_time'] = trip_data['depart']
                    if order == len(trip_data['stops']) and trip_data.get('arrive'):
                        defaults['arrival_time'] = trip_data['arrive']
                    TripStop.objects.update_or_create(trip=trip, city=stop_city, defaults=defaults)

                TripFare.objects.update_or_create(
                    trip=trip,
                    from_city=start_city,
                    to_city=end_city,
                    defaults={'price': trip_data['price'], 'currency': 'UAH'},
                )

                self.stdout.write(self.style.SUCCESS(f'Added trip: {trip.title} | price {trip_data["price"]} UAH'))

    def _get_or_create_city(self, name):
        return City.objects.get_or_create(name=name, defaults={'country': 'UA'})[0]
