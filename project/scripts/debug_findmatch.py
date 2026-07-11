import re
import unicodedata
import difflib
from main.models import Trip, Route, City, TripStop


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
if not route:
    print('No route')
    raise SystemExit
trip = Trip.objects.filter(route=route).first()
print('Trip', trip.id, trip.title)
trip_stops = list(trip.trip_stops.select_related('city').all())
for s in trip_stops:
    print('stop', s.order, s.city.name if s.city else None, 'addr', s.address, 'arr', s.arrival_time, 'dep', s.departure_time)

token = 'Варшава'
q_to = normalize_text(token)
print('q_to', q_to)
city_obj = City.objects.filter(name__iexact=token).first()
print('city_obj exact', city_obj, getattr(city_obj,'id',None))
all_cities = list(City.objects.all())
city_norm_map = {normalize_text(c.name): c for c in all_cities}
print('city_norm_map has варшава', 'варшава' in city_norm_map)
if not city_obj:
    city_obj = city_norm_map.get(q_to)
print('city_obj normalized', city_obj, getattr(city_obj,'id',None))

cand_ids = {city_obj.id} if city_obj else set()
print('cand_ids', cand_ids)
for s in trip_stops:
    cid = s.city.id if s.city else None
    if cid in cand_ids:
        print('match stop by city', s.order, s.city.name, s.address)

for s in trip_stops:
    city_name_raw = s.city.name if s.city else ''
    city_name = normalize_text(city_name_raw)
    addr = normalize_text(s.address or '')
    print('check', s.order, city_name, addr)
    if city_name and (q_to in city_name or city_name in q_to):
        print(' direct name match', s.order, city_name)
    if addr and (q_to in addr or addr in q_to):
        print(' addr match', s.order, addr)
    if city_name and similar(q_to, city_name) >= 0.60:
        print(' fuzzy match', s.order, city_name, similar(q_to, city_name))
    if addr and similar(q_to, addr) >= 0.60:
        print(' fuzzy addr', s.order, addr, similar(q_to, addr))
