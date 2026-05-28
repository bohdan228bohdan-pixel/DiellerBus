from django.db import migrations


def create_test_trips(apps, schema_editor):
    City = apps.get_model('main', 'City')
    Route = apps.get_model('main', 'Route')
    Trip = apps.get_model('main', 'Trip')
    TripStop = apps.get_model('main', 'TripStop')
    TripFare = apps.get_model('main', 'TripFare')

    # Create cities (or get existing)
    lutsk, _ = City.objects.get_or_create(name='Луцьк', defaults={'country': 'UA'})
    warsaw, _ = City.objects.get_or_create(name='Варшава', defaults={'country': 'PL'})
    kyiv, _ = City.objects.get_or_create(name='Київ', defaults={'country': 'UA'})
    lodz, _ = City.objects.get_or_create(name='Лодзь', defaults={'country': 'PL'})

    # Route: Луцьк — Варшава
    route1, _ = Route.objects.get_or_create(name='Луцьк — Варшава', defaults={'description': 'Тестовий маршрут', 'active': True})
    trip1, _ = Trip.objects.get_or_create(route=route1, title='Тест: Луцьк-Варшава', defaults={
        'carrier': 'Тестовий Перевізник 1',
        'base_price': 1.00,
        'currency': 'UAH',
        'direction': 'UA_PL',
        'is_international': True,
        'active': True,
        'seats': 50,
    })

    # Trip stops for trip1
    TripStop.objects.get_or_create(trip=trip1, city=lutsk, order=0, defaults={'price': 0.00})
    TripStop.objects.get_or_create(trip=trip1, city=warsaw, order=1, defaults={'price': 1.00})

    # Fare for trip1: Луцьк -> Варшава = 1 UAH
    TripFare.objects.get_or_create(trip=trip1, from_city=lutsk, to_city=warsaw, defaults={'price': 1.00, 'currency': 'UAH'})

    # Route: Київ — Лодзь
    route2, _ = Route.objects.get_or_create(name='Київ — Лодзь', defaults={'description': 'Тестовий маршрут', 'active': True})
    trip2, _ = Trip.objects.get_or_create(route=route2, title='Тест: Київ-Лодзь', defaults={
        'carrier': 'Тестовий Перевізник 2',
        'base_price': 2.00,
        'currency': 'UAH',
        'direction': 'UA_PL',
        'is_international': True,
        'active': True,
        'seats': 50,
    })

    # Trip stops for trip2
    TripStop.objects.get_or_create(trip=trip2, city=kyiv, order=0, defaults={'price': 0.00})
    TripStop.objects.get_or_create(trip=trip2, city=lodz, order=1, defaults={'price': 2.00})

    # Fare for trip2: Київ -> Лодзь = 2 UAH
    TripFare.objects.get_or_create(trip=trip2, from_city=kyiv, to_city=lodz, defaults={'price': 2.00, 'currency': 'UAH'})


def remove_test_trips(apps, schema_editor):
    Route = apps.get_model('main', 'Route')
    Trip = apps.get_model('main', 'Trip')

    # Remove trips/routes created by this migration (titles prefixed with 'Тест:')
    try:
        trips = Trip.objects.filter(title__startswith='Тест:')
        for t in trips:
            t.delete()
    except Exception:
        pass

    try:
        Route.objects.filter(name__in=['Луцьк — Варшава', 'Київ — Лодзь']).delete()
    except Exception:
        pass


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0024_ticket_used_fields'),
    ]

    operations = [
        migrations.RunPython(create_test_trips, remove_test_trips),
    ]
