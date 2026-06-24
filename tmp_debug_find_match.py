import unicodedata, re, difflib
from main.models import Trip, City


def normalize_text(s):
    if not s:
        return ''
    s = str(s).strip().lower()
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r'[^0-9a-z\u0400-\u04FF\s]', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


t = Trip.objects.get(id=1)
trip_stops = list(t.trip_stops.select_related('city').all())
route_stops = list(t.route.stops.select_related('city').all())
print('trip stops:', [(s.order, s.city.id, s.city.name) for s in trip_stops])
print('route stops:', [(s.order, s.city.id, s.city.name) for s in route_stops])


def find_match_local(stops_list, token):
    token_norm = normalize_text(token)
    print('\nfind_match_local token', token, 'token_norm', token_norm)
    if not token_norm:
        return None
    city_obj = None
    try:
        city_obj = City.objects.filter(name__iexact=token).first()
    except Exception:
        city_obj = None
    print('city_obj exact:', city_obj and (city_obj.id, city_obj.name, city_obj.parent_id))
    if not city_obj:
        city_obj = {normalize_text(c.name): c for c in City.objects.all()}.get(token_norm)
    print('city_obj norm:', city_obj and (city_obj.id, city_obj.name, city_obj.parent_id))

    if city_obj:
        cand_ids = {city_obj.id}
        include_sub = bool(getattr(getattr(t, 'route', None), 'include_subcities', False))
        print('include_sub', include_sub)
        if include_sub:
            cur = city_obj
            while cur and getattr(cur, 'parent', None):
                cur = cur.parent
                if cur and getattr(cur, 'id', None):
                    cand_ids.add(cur.id)
            if getattr(city_obj, 'parent', None) is None:
                for ch in city_obj.subcities.all():
                    cand_ids.add(ch.id)
        print('cand_ids', cand_ids)
        for s in stops_list:
            try:
                print('checking stop', s.order, s.city.id, s.city.name, 'in cand_ids?', s.city.id in cand_ids)
                if getattr(s, 'city', None) and getattr(s.city, 'id', None) in cand_ids:
                    print('MATCH STOP', s.order, s.city.id, s.city.name)
                    return s
            except Exception as e:
                print('stop check exception', e)
                continue

    # fallback match by name/address
    for s in stops_list:
        city_name_raw = (s.city.name if getattr(s, 'city', None) else '') or ''
        city_name = normalize_text(city_name_raw)
        addr = normalize_text((s.address or ''))
        if city_name and (token_norm in city_name or city_name in token_norm):
            print('fallback by city substring', s.order, s.city.name)
            return s
        if addr and (token_norm in addr or addr in token_norm):
            print('fallback by addr substring', s.order, s.address)
            return s
        try:
            if city_name and difflib.SequenceMatcher(None, token_norm, city_name).ratio() >= 0.60:
                print('fallback by fuzzy city', s.order, s.city.name)
                return s
            if addr and difflib.SequenceMatcher(None, token_norm, addr).ratio() >= 0.60:
                print('fallback by fuzzy addr', s.order, s.address)
                return s
        except Exception:
            pass
    return None

print(find_match_local(trip_stops, 'Копачівка'))
print(find_match_local(route_stops, 'Копачівка'))
print(find_match_local(trip_stops, 'Пулави'))
print(find_match_local(route_stops, 'Пулави'))
