import unicodedata, re, difflib
from main.models import Trip

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


t = Trip.objects.get(pk=1)
from_token = t.trip_stops.order_by('order').first().city.name
to_token = t.trip_stops.order_by('order').last().city.name
print('from_token raw:', from_token)
print('to_token raw:', to_token)

q_from = normalize_text(from_token)
q_to = normalize_text(to_token)
print('q_from', q_from, 'q_to', q_to)

trip_stops = list(t.trip_stops.select_related('city').all())
route_stops = list(t.route.stops.select_related('city').all()) if t.route_id else []


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
print('from_stop city:', getattr(from_stop, 'city', None).name if getattr(from_stop, 'city', None) else None)
print('to_stop city:', getattr(to_stop, 'city', None).name if getattr(to_stop, 'city', None) else None)
print('orders', getattr(from_stop, 'order', None), getattr(to_stop, 'order', None))
