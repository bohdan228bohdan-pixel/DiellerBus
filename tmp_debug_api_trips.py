import unicodedata, re, difflib
from main.models import Trip, City, TripDayAvailability


def normalize_text(s):
    if not s:
        return ''
    s = str(s).strip().lower()
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r'[^0-9a-z\u0400-\u04FF\s]', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

from_name='Копачівка'
from_norm = normalize_text(from_name)
from_token = from_name

to_name='Пулави'
to_norm = normalize_text(to_name)

# build city_norm_map
city_norm_map = {normalize_text(c.name): c for c in City.objects.all()}

trips_qs = Trip.objects.filter(active=True).select_related('route')
print('total active trips', trips_qs.count())

for trip in trips_qs:
    print('\n--- Trip', trip.id, trip.route.name if trip.route else None, 'date', trip.date, 'direction', trip.direction)
    trip_stops = list(trip.trip_stops.select_related('city').all())
    route_stops = list(trip.route.stops.select_related('city').all()) if trip.route_id else []

    def similar(a,b):
        try:
            return difflib.SequenceMatcher(None,a,b).ratio()
        except Exception:
            return 0.0

    def find_match_local(stops_list, token):
        token_norm = normalize_text(token)
        if not token_norm:
            return None
        city_obj = None
        try:
            city_obj = City.objects.filter(name__iexact=token).first()
        except Exception:
            city_obj=None
        if not city_obj:
            city_obj = city_norm_map.get(token_norm)
        if city_obj:
            cand_ids = {city_obj.id}
            include_sub = bool(getattr(getattr(trip,'route',None),'include_subcities',False))
            if include_sub:
                cur = city_obj
                while cur and getattr(cur,'parent',None):
                    cur = cur.parent
                    if cur and getattr(cur,'id',None): cand_ids.add(cur.id)
                if getattr(city_obj,'parent',None) is None:
                    for ch in city_obj.subcities.all():
                        cand_ids.add(ch.id)
            for s in stops_list:
                if getattr(s,'city',None) and getattr(s.city,'id',None) in cand_ids:
                    return s
        for s in stops_list:
            city_name_raw = (s.city.name if getattr(s,'city',None) else '') or ''
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

    f1 = find_match_local(trip_stops, from_name) or find_match_local(route_stops, from_name)
    t1 = find_match_local(trip_stops, to_name) or find_match_local(route_stops, to_name)
    print('from match', f1 and (f1.order, f1.city.id, f1.city.name))
    print('to match', t1 and (t1.order, t1.city.id, t1.city.name))

    # Check availability logic for date 2026-06-25
    import datetime
    date_obj = datetime.date(2026,6,25)
    if date_obj:
        unavail_qs = TripDayAvailability.objects.filter(trip=trip, date=date_obj, available=False)
        avail_qs = TripDayAvailability.objects.filter(trip=trip, date=date_obj, available=True)
        unavailable_on_date = unavail_qs.exists()
        available_on_date = avail_qs.exists()
        include_trip = (not unavailable_on_date) and (trip.date is None or trip.date == date_obj or available_on_date)
    else:
        include_trip = True
    print('available_on_2026-06-25:', include_trip, 'unavailable_on_date', unavailable_on_date if date_obj else None, 'available_on_date', available_on_date if date_obj else None)

    if f1 and t1 and include_trip:
        print('WOULD ADD TRIP', trip.id)
    else:
        print('WOULD NOT ADD TRIP')
