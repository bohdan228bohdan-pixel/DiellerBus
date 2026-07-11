import re
import unicodedata
import difflib
from main.models import Trip, Route, City, TripStop, TripFare
from django.db.models import Q


def normalize_text(s):
    if not s:
        return ''
    s = str(s).strip().lower()
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r'[^0-9a-z\u0400-\u04FF\s]', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def similar(a, b):
    try:
        return difflib.SequenceMatcher(None, a, b).ratio()
    except Exception:
        return 0.0

route = Route.objects.filter(name__icontains='Тернопіль — Варшава').first()
print('route', route)
trip = Trip.objects.filter(route=route).first()
print('trip', trip.id, trip.title)
trip_stops = list(trip.trip_stops.select_related('city').all())
route_stops = list(trip.route.stops.select_related('city').all())

q_from = normalize_text('Тернопіль')
q_to = normalize_text('Варшава')
print('q_from', q_from, 'q_to', q_to)

city_norm_map = {normalize_text(c.name): c for c in City.objects.all()}

for purpose, token in [('from', q_from), ('to', q_to)]:
    print('\n---', purpose, token)
    city_obj = City.objects.filter(name__iexact=(token or '')).first()
    print('exact city_obj', city_obj)
    if not city_obj:
        city_obj = city_norm_map.get(token)
    print('normalized city_obj', city_obj)
    cand_ids = {city_obj.id} if city_obj else set()
    print('cand_ids', cand_ids)
    for s in trip_stops:
        if s.city and s.city.id in cand_ids:
            print('trip stop match by city', purpose, s.order, s.city.name, s.address)
    for s in route_stops:
        if s.city and s.city.id in cand_ids:
            print('route stop match by city', purpose, s.order, s.city.name, s.address)
    for s in trip_stops:
        city_name_raw = s.city.name if s.city else ''
        city_name = normalize_text(city_name_raw)
        addr = normalize_text(s.address or '')
        if city_name and (token in city_name or city_name in token):
            print('direct name', purpose, s.order, city_name_raw)
        if addr and (token in addr or addr in token):
            print('direct addr', purpose, s.order, s.address)
        if city_name and similar(token, city_name) >= 0.60:
            print('fuzzy name', purpose, s.order, city_name_raw, similar(token, city_name))
        if addr and similar(token, addr) >= 0.60:
            print('fuzzy addr', purpose, s.order, s.address, similar(token, addr))

# simulate find_match order logic

for purpose, token, stops_list in [('from', q_from, trip_stops), ('to', q_to, trip_stops)]:
    print('\n=== simulate', purpose)
    city_obj = City.objects.filter(name__iexact=(token or '')).first()
    if not city_obj:
        city_obj = city_norm_map.get(token)
    if city_obj:
        cand_ids = {city_obj.id}
        for s in stops_list:
            if s.city and s.city.id in cand_ids:
                print('stop by city', s.order, s.city.name)
                break
    for s in stops_list:
        city_name_raw = s.city.name if s.city else ''
        city_name = normalize_text(city_name_raw)
        addr = normalize_text(s.address or '')
        if city_name and (token in city_name or city_name in token):
            print('stop by name', s.order, city_name_raw)
            break
        if addr and (token in addr or addr in token):
            print('stop by addr', s.order, s.address)
            break
        if city_name and similar(token, city_name) >= 0.60:
            print('stop by fuzzy', s.order, city_name_raw)
            break
        if addr and similar(token, addr) >= 0.60:
            print('stop by fuzzy addr', s.order, s.address)
            break

print('\nDirect fare check')
direct = TripFare.objects.filter(trip=trip, from_city__name='Тернопіль', to_city__name='Варшава').first()
print('direct', direct, direct.price if direct else None)
if direct:
    direct.price = 2070.00
    direct.save()
    print('updated', direct.price)

trip.base_price = 2070.00
trip.save()
print('trip base_price', trip.base_price)
print('final stops list count', len(trip_stops))
for s in trip_stops:
    print(s.order, s.city.name, s.address, s.arrival_time, s.departure_time)
