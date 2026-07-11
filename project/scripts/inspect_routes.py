from main.models import Trip, Route, City, TripFare
from django.db.models import Q

route = Route.objects.filter(name__icontains='Тернопіль — Варшава').first()
print('Route', route, 'active', route.active if route else None)
if route:
    trips = Trip.objects.filter(route=route).order_by('date','title')
    print('Trips count', trips.count())
    for t in trips:
        print('TRIP', t.id, t.title, t.date, t.base_price, t.currency, t.direction, t.active)
        print('  fares:')
        for f in TripFare.objects.filter(trip=t):
            print('   ', f.from_city.name, '->', f.to_city.name, f.price, f.currency)
        print('  stops:')
        for s in t.trip_stops.order_by('order'):
            print('   ', s.order, s.city.name, s.address, s.arrival_time, s.departure_time, s.price)

print('\nCities with blank or bad country:')
for c in City.objects.filter(Q(country='')|Q(country__isnull=True)).order_by('name')[:100]:
    print(' ', c.name, repr(c.country))

print('\nCities containing Львів:')
for c in City.objects.filter(name__icontains='Львів')[:100]:
    print(' ', c.name, c.country)
print('\nCities containing Рівне:')
for c in City.objects.filter(name__icontains='Рівне')[:100]:
    print(' ', c.name, c.country)
print('\nCities containing Луцьк:')
for c in City.objects.filter(name__icontains='Луцьк')[:100]:
    print(' ', c.name, c.country)
