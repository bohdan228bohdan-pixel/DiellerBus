import random
from django.shortcuts import render, redirect
from django.http import HttpResponse, HttpResponseRedirect
from django.contrib.auth import authenticate, login as auth_login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.mail import send_mail
import re
import unicodedata
import difflib

from .models import EmailVerification
from django.http import JsonResponse
from .models import City, Route, RouteStop, Trip, TripStop


# ========================
# СТАТИЧНІ СТОРІНКИ
# ========================

def home(request):
    return render(request, 'index.html')


def about(request):
    return HttpResponse('<h1>About Page</h1>')


def bova(request):
    return render(request, 'bova.html')


def eos(request):
    return render(request, 'eos.html')


def kvitokindex(request):
    return render(request, 'kvitokindex.html')


def cities_table(request):
    """Render a simple page that shows all cities from the DB in a table."""
    return render(request, 'cities_table.html')


def mercedes2(request):
    return render(request, 'mercedes2.html')


def nashbusindex(request):
    return render(request, 'nashbusindex.html')


def neolplanwhite(request):
    return render(request, 'neolplanwhite.html')


def neoplanred(request):
    return render(request, 'Neoplanred.html')


def oplata(request):
    return render(request, 'oplata.html')


def api_trips(request):
    """API endpoint returning available trips between two cities.

    Query params: from, to, date, dir, pax
    """
    from_name = request.GET.get('from') or request.GET.get('from_city') or request.GET.get('fromCity')
    to_name = request.GET.get('to') or request.GET.get('to_city') or request.GET.get('toCity')
    direction = request.GET.get('dir')
    pax = int(request.GET.get('pax') or request.GET.get('passengers') or 1)

    results = []

    if not from_name or not to_name:
        return JsonResponse({'trips': results})

    # normalize search tokens
    def normalize_text(s):
        if not s:
            return ''
        s = str(s).strip().lower()
        s = unicodedata.normalize('NFKD', s)
        s = ''.join(ch for ch in s if not unicodedata.combining(ch))
        # keep cyrillic/latin letters, numbers and spaces
        s = re.sub(r'[^0-9a-z\u0400-\u04FF\s]', '', s)
        s = re.sub(r'\s+', ' ', s).strip()
        return s

    def similar(a, b):
        try:
            return difflib.SequenceMatcher(None, a, b).ratio()
        except Exception:
            return 0.0

    q_from = normalize_text(from_name)
    q_to = normalize_text(to_name)

    trips_qs = Trip.objects.filter(active=True)
    if direction:
        trips_qs = trips_qs.filter(direction=direction)

    # iterate trips and try flexible matching: match by TripStop city/address or RouteStop
    for trip in trips_qs.select_related('route'):
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
                # direct substring matches
                if token_norm in city_name or city_name in token_norm:
                    return s
                if token_norm in addr or addr in token_norm:
                    return s
                # fuzzy match fallback
                if city_name and similar(token_norm, city_name) >= 0.60:
                    return s
                if addr and similar(token_norm, addr) >= 0.60:
                    return s
            return None

        from_stop = find_match(trip_stops, q_from) or find_match(route_stops, q_from)
        to_stop = find_match(trip_stops, q_to) or find_match(route_stops, q_to)

        # Fallback: if trip has explicit start/end cities, allow matching by them
        try:
            if (not from_stop) and getattr(trip, 'start_city', None):
                sc = trip.start_city.name.lower() if trip.start_city and trip.start_city.name else ''
                if sc and (q_from in sc or sc in q_from):
                    # prefer trip stop matching the start_city
                    from_stop = trip.trip_stops.filter(city=trip.start_city).select_related('city').first() or None
                    if not from_stop and route_stops:
                        from_stop = next((s for s in route_stops if getattr(s.city, 'id', None) == trip.start_city.id), None)

            if (not to_stop) and getattr(trip, 'end_city', None):
                ec = trip.end_city.name.lower() if trip.end_city and trip.end_city.name else ''
                if ec and (q_to in ec or ec in q_to):
                    to_stop = trip.trip_stops.filter(city=trip.end_city).select_related('city').first() or None
                    if not to_stop and route_stops:
                        to_stop = next((s for s in route_stops if getattr(s.city, 'id', None) == trip.end_city.id), None)
        except Exception:
            # defensive: if any relation is missing, continue without fallback
            pass

        if not from_stop or not to_stop:
            # no match for this trip
            continue

        from_order = getattr(from_stop, 'order', None)
        to_order = getattr(to_stop, 'order', None)
        if from_order is None or to_order is None:
            continue

        # ensure correct ordering depending on trip direction
        # If orders are equal but trip has explicit start/end cities, allow when they match the searched endpoints
        if trip.direction == 'UA_PL':
            if from_order > to_order:
                continue
            if from_order == to_order:
                # equal ordering - allow only if start/end cities are explicit and match
                if not (getattr(trip, 'start_city', None) and getattr(trip, 'end_city', None)):
                    continue
                try:
                    if not (getattr(from_stop, 'city', None) and getattr(to_stop, 'city', None) and from_stop.city_id == trip.start_city_id and to_stop.city_id == trip.end_city_id):
                        continue
                except Exception:
                    continue
        if trip.direction == 'PL_UA':
            if from_order < to_order:
                continue
            if from_order == to_order:
                if not (getattr(trip, 'start_city', None) and getattr(trip, 'end_city', None)):
                    continue
                try:
                    if not (getattr(from_stop, 'city', None) and getattr(to_stop, 'city', None) and from_stop.city_id == trip.start_city_id and to_stop.city_id == trip.end_city_id):
                        continue
                except Exception:
                    continue

        # choose which stops list to use for building the segment (prefer trip_stops for times)
        base_stops = trip_stops if trip_stops else route_stops
        segment = [s for s in base_stops if s.order >= from_order and s.order <= to_order]

        stops_list = []
        for s in segment:
            dep = getattr(s, 'departure_time', None)
            arr = getattr(s, 'arrival_time', None)
            time = None
            if dep:
                time = dep.strftime('%H:%M')
            elif arr:
                time = arr.strftime('%H:%M')
            place = s.address or (s.city.name if getattr(s, 'city', None) else '')
            stops_list.append({'time': time or '', 'place': place})

        price = float(trip.base_price or 0.0)
        discount = int(trip.discount_percent or 0)
        price_after = round(price * (1.0 - discount / 100.0), 2)

        depart_time = ''
        arrive_time = ''
        from_dep = getattr(from_stop, 'departure_time', None) or getattr(from_stop, 'arrival_time', None)
        to_arr = getattr(to_stop, 'arrival_time', None) or getattr(to_stop, 'departure_time', None)
        if from_dep:
            depart_time = from_dep.strftime('%H:%M')
        if to_arr:
            arrive_time = to_arr.strftime('%H:%M')

        results.append({
            'id': trip.id,
            'route': trip.route.name,
            'title': trip.title,
            'direction': trip.direction,
            'from': from_name,
            'from_place': from_stop.address or (from_stop.city.name if getattr(from_stop, 'city', None) else from_name),
            'to': to_name,
            'to_place': to_stop.address or (to_stop.city.name if getattr(to_stop, 'city', None) else to_name),
            'depart': depart_time,
            'arrive': arrive_time,
            'duration': '',
            'price': price,
            'currency': trip.currency,
            'discount_percent': discount,
            'price_after_discount': price_after,
            'free': trip.seats,
            'carrier': trip.carrier or trip.title or trip.route.name,
            'stops': stops_list,
        })

    return JsonResponse({'trips': results})


def api_cities(request):
    """Return available cities grouped by country code (UA/PL).

    Optional query param: dir (UA_PL or PL_UA) — currently unused but accepted.
    """
    direction = request.GET.get('dir')

    # prefer explicit country flags; fall back to any cities present
    ua_qs = City.objects.filter(country__iexact='UA').order_by('name')
    pl_qs = City.objects.filter(country__iexact='PL').order_by('name')

    # if countries are not set, include cities referenced by trips
    if not ua_qs.exists() or not pl_qs.exists():
        ua_ids = set(TripStop.objects.filter(trip__direction='UA_PL').values_list('city_id', flat=True))
        pl_ids = set(TripStop.objects.filter(trip__direction='PL_UA').values_list('city_id', flat=True))
        ua_qs = City.objects.filter(id__in=ua_ids).order_by('name') if ua_ids else ua_qs
        pl_qs = City.objects.filter(id__in=pl_ids).order_by('name') if pl_ids else pl_qs

    ua_list = [{'id': c.id, 'name': c.name} for c in ua_qs]
    pl_list = [{'id': c.id, 'name': c.name} for c in pl_qs]

    return JsonResponse({'UA': ua_list, 'PL': pl_list})


@login_required
def profile(request):
    return render(request, 'profile.html')


def redirect_to_home(request):
    return HttpResponseRedirect('/home/')


# ========================
# РЕЄСТРАЦІЯ + ЛОГІН
# ========================

def registerindex(request):
    context = {}

    if request.method == "POST":

        # ========================
        # РЕЄСТРАЦІЯ
        # ========================
        if "repeatpass" in request.POST:
            username = request.POST.get("login")
            email = request.POST.get("emeil")
            password = request.POST.get("password")
            repeat = request.POST.get("repeatpass")

            if password != repeat:
                context["register_error"] = "Паролі не співпадають"
                return render(request, "registerindex.html", context)

            if User.objects.filter(username=username).exists():
                context["register_error"] = "Такий логін вже існує"
                return render(request, "registerindex.html", context)

            if User.objects.filter(email=email).exists():
                context["register_error"] = "Така пошта вже використовується"
                return render(request, "registerindex.html", context)

            # ❗ Створюємо НЕАКТИВНОГО користувача
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                is_active=False
            )

            # 6-значний код
            code = str(random.randint(100000, 999999))

            EmailVerification.objects.create(
                user=user,
                code=code
            )

            send_mail(
                "Підтвердження email — Dieller Bus",
                f"Ваш код підтвердження: {code}",
                None,
                [email],
                fail_silently=False,
            )

            request.session["verify_user_id"] = user.id
            return redirect("verify_email")

        # ========================
        # ЛОГІН
        # ========================
        else:
            username = request.POST.get("login")
            password = request.POST.get("password")

            user = authenticate(request, username=username, password=password)

            if user is None:
                context["login_error"] = "Невірний логін або пароль"
                return render(request, "registerindex.html", context)

            if not user.is_active:
                context["login_error"] = "Підтвердіть email перед входом"
                return render(request, "registerindex.html", context)

            auth_login(request, user)
            return redirect("home")

    return render(request, "registerindex.html", context)


# ========================
# ПІДТВЕРДЖЕННЯ EMAIL
# ========================

def verify_email(request):
    user_id = request.session.get("verify_user_id")

    if not user_id:
        return redirect("registerindex")

    user = User.objects.get(id=user_id)
    verification = EmailVerification.objects.get(user=user)

    if request.method == "POST":
        code = request.POST.get("code")

        if verification.code == code:
            user.is_active = True
            user.save()
            verification.delete()

            auth_login(request, user)
            return redirect("profile")

        return render(request, "verify_email.html", {
            "error": "Невірний код"
        })

    return render(request, "verify_email.html")

import stripe
from django.conf import settings
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from .models import Ticket

stripe.api_key = settings.STRIPE_SECRET_KEY


@login_required
def create_ticket(request):
    if request.method == "POST":
        from_city = request.POST["from"]
        to_city = request.POST["to"]
        passengers = int(request.POST["passengers"])
        travel_date = request.POST["date"]
        price = int(request.POST["price"])  # грн

        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "uah",
                    "product_data": {
                        "name": f"Квиток {from_city} → {to_city}",
                    },
                    "unit_amount": price * 100,
                },
                "quantity": passengers,
            }],
            mode="payment",
            success_url=request.build_absolute_uri("/payment-success/"),
            cancel_url=request.build_absolute_uri("/payment-cancel/"),
        )

        Ticket.objects.create(
            user=request.user,
            from_city=from_city,
            to_city=to_city,
            travel_date=travel_date,
            passengers=passengers,
            price=price * passengers,
            stripe_session_id=session.id
        )

        return redirect(session.url)

    return redirect("home")


@login_required
def payment_success(request):
    return render(request, "payment_success.html")


@login_required
def payment_cancel(request):
    return render(request, "payment_cancel.html")

import stripe
from django.conf import settings
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from .models import Ticket

stripe.api_key = settings.STRIPE_SECRET_KEY


def kvitokindex(request):
    return render(request, "kvitokindex.html")


@login_required
def create_ticket(request):
    if request.method == "POST":
        from_city = request.POST["from_city"]
        to_city = request.POST["to_city"]
        passengers = int(request.POST["passengers"])
        travel_date = request.POST["travel_date"]
        price = int(request.POST["price"])  # за 1 квиток

        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "uah",
                    "product_data": {
                        "name": f"Квиток {from_city} → {to_city}",
                    },
                    "unit_amount": price * 100,
                },
                "quantity": passengers,
            }],
            mode="payment",
            success_url=request.build_absolute_uri("/payment-success/"),
            cancel_url=request.build_absolute_uri("/payment-cancel/"),
        )

        Ticket.objects.create(
            user=request.user,
            from_city=from_city,
            to_city=to_city,
            travel_date=travel_date,
            passengers=passengers,
            total_price=price * passengers,
            stripe_session_id=session.id
        )

        return redirect(session.url)

    return redirect("kvitokindex")


@login_required
def payment_success(request):
    return render(request, "payment_success.html")


@login_required
def payment_cancel(request):
    return render(request, "payment_cancel.html")


@login_required
def profile(request):
    tickets = Ticket.objects.filter(user=request.user)
    return render(request, "profile.html", {"tickets": tickets})

def oplata(request):
    return render(request, "oplata.html")