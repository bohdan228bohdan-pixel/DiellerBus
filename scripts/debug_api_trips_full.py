from main.models import Trip, TripStop, TripDayAvailability
import unicodedata, re, difflib
from datetime import datetime
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

from_name = 'Луцьк'
to_name = 'Пулави'
date_str = '2026-05-10'

try:
    date_obj = datetime.fromisoformat(date_str).date()
except Exception:
    try:
        date_obj = datetime.strptime(date_str, '%d.%m.%Y').date()
    except Exception:
        date_obj = None

q_from = normalize_text(from_name)
q_to = normalize_text(to_name)
print('Normalized:', q_from, q_to)

trips_qs = Trip.objects.filter(active=True)
if date_obj:
    trips_qs = trips_qs.filter(Q(date__isnull=True) | Q(date=date_obj)).exclude(day_availabilities__date=date_obj, day_availabilities__available=False)

print('Trips matching date filter:', trips_qs.count(), list(trips_qs.values_list('id', flat=True)))

results = []
for trip in trips_qs.select_related('route'):
    print('\nTrip', trip.id, trip.route.name if trip.route else '', 'trip.date', trip.date)
    trip_stops = list(trip.trip_stops.select_related('city').all())
    route_stops = list(trip.route.stops.select_related('city').all()) if trip.route_id else []

    def find_match(stops_list, token):
        token_norm = normalize_text(token)
        if not token_norm:
            return None
        for s in stops_list:
            city_name_raw = (s.city.name if getattr(s, 'city', None) else '') or ''
            city_name = normalize_text(city_name_raw)
            addr = normalize_text((s.address or ''))
            if city_name and (token_norm in city_name or city_name in token_norm):
                return s
            if addr and (token_norm in addr or addr in token_norm):
                return s
            if city_name and similar(token_norm, city_name) >= 0.60:
                return s
            if addr and similar(token_norm, addr) >= 0.60:
                return s
        return None

    from_stop = find_match(trip_stops, q_from) or find_match(route_stops, q_from)
    to_stop = find_match(trip_stops, q_to) or find_match(route_stops, q_to)
    print(' from_stop:', getattr(from_stop, 'city', None).name if getattr(from_stop, 'city', None) else None)
    print(' to_stop:', getattr(to_stop, 'city', None).name if getattr(to_stop, 'city', None) else None)

    if not from_stop or not to_stop:
        print(' Skipped: missing from/to stop')
        continue

    from_order = getattr(from_stop, 'order', None)
    to_order = getattr(to_stop, 'order', None)
    print(' orders', from_order, to_order)

    if trip.direction in ('UA_PL', 'UA_UA'):
        if from_order > to_order:
            print(' Skipped: ordering mismatch for UA_PL/UA_UA')
            continue
        if from_order == to_order:
            if not (getattr(trip, 'start_city', None) and getattr(trip, 'end_city', None)):
                print(' Skipped: equal order and no explicit start/end')
                continue
            try:
                if not (getattr(from_stop, 'city', None) and getattr(to_stop, 'city', None) and from_stop.city_id == trip.start_city_id and to_stop.city_id == trip.end_city_id):
                    print(' Skipped: equal order but start/end mismatch')
                    continue
            except Exception:
                print(' Skipped: exception in equal-order check')
                continue
    elif trip.direction == 'PL_UA':
        if from_order < to_order:
            print(' Skipped: ordering mismatch for PL_UA')
            continue
        if from_order == to_order:
            if not (getattr(trip, 'start_city', None) and getattr(trip, 'end_city', None)):
                print(' Skipped: equal order and no explicit start/end (PL_UA)')
                continue
            try:
                if not (getattr(from_stop, 'city', None) and getattr(to_stop, 'city', None) and from_stop.city_id == trip.start_city_id and to_stop.city_id == trip.end_city_id):
                    print(' Skipped: equal order but start/end mismatch (PL_UA)')
                    continue
            except Exception:
                print(' Skipped: exception in equal-order check (PL_UA)')
                continue

    print(' Passed matching checks — will be included')
    results.append(trip.id)

print('\nFinal matched trips:', results)
