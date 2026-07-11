from main.models import Trip, Route, TripFare

route = Route.objects.filter(name__icontains='Тернопіль — Варшава').first()
if not route:
    print('Route not found')
else:
    trips = Trip.objects.filter(route=route)
    for t in trips:
        print('UPDATING TRIP', t.id, t.title, t.base_price)
        for fare in TripFare.objects.filter(trip=t):
            old = float(fare.price)
            new = round(old * 1.15, 2)
            fare.price = new
            fare.save()
            print('  fare', fare.from_city.name, '->', fare.to_city.name, old, '->', new)
        t.base_price = round(float(t.base_price) * 1.15, 2)
        t.save()
        print('  base_price', t.base_price)
    print('DONE')
