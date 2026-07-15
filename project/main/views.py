import random
from urllib.parse import urlencode
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie, csrf_protect
from django.middleware.csrf import get_token
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.mail import send_mail, EmailMessage, EmailMultiAlternatives
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.utils import timezone
import re
import unicodedata
import difflib
from django.conf import settings
from .models import Ticket, Payment, Carrier, Bus, SupportTicket, SupportMessage, SupportPresetQuestion
from .wayforpay import get_wayforpay_service, verify_wayforpay_signature, build_wayforpay_callback_response
from django.views.generic import ListView, DetailView, FormView
from django.urls import reverse_lazy, reverse
from .forms import BusBookingForm
from .forms import SupportTicketForm, SupportMessageForm, TicketEditForm, PassengerFormSet, ProfileForm, SupportUserForm
from .forms import RequestPasswordChangeForm

# Additional imports for PDF generation and email attachments
import json
import base64
import hashlib
import logging
import io
import csv
import zipfile
from django.template.loader import render_to_string
import hmac
import os
from django.db import IntegrityError, transaction
from decimal import Decimal
try:


    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.lib import colors
    from reportlab.lib.units import mm
except Exception:
    # reportlab may be missing in development environment; PDF generation will fail until installed
    A4 = None


def _ticket_signature(ticket):
    """Return a URL-safe signature for a ticket to be used in verification links.

    Uses HMAC-SHA256 with Django SECRET_KEY and ticket stable fields.
    """
    try:
        key = settings.SECRET_KEY.encode('utf-8')
        # include paid state so signatures become invalid when payment state changes
        paid_flag = '1' if getattr(ticket, 'paid', False) else '0'
        msg = f"{ticket.id}:{ticket.total_price}:{ticket.user_id}:{paid_flag}".encode('utf-8')
        dig = hmac.new(key, msg, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(dig).decode('utf-8').rstrip('=')
    except Exception:
        return ''


def _verify_ticket_signature(ticket, sig):
    try:
        if not sig:
            return False
        expected = _ticket_signature(ticket)
        return hmac.compare_digest(expected, sig)
    except Exception:
        return False


def _get_wayforpay_service(request=None):
    merchant_login = getattr(settings, 'WAYFORPAY_MERCHANT_LOGIN', None) or ''
    merchant_domain = getattr(settings, 'WAYFORPAY_DOMAIN', None) or (request.get_host().split(':')[0] if request else '') or 'localhost'
    return get_wayforpay_service(
        merchant_login=merchant_login,
        merchant_domain=merchant_domain,
        return_url=getattr(settings, 'WAYFORPAY_RETURN_URL', None) or (request.build_absolute_uri(reverse('main:payment_success')) if request else ''),
        callback_url=getattr(settings, 'WAYFORPAY_CALLBACK_URL', None) or (request.build_absolute_uri(reverse('main:wayforpay_callback')) if request else ''),
    )


def _normalize_wayforpay_list_field(values):
    if values is None:
        return []
    if isinstance(values, (list, tuple)):
        return [str(v) for v in values]
    return [str(values)]


def _mark_ticket_paid(ticket, payment, request=None, provider='wayforpay', provider_payment_id=None, payload=None):
    from .models import Payment
    if not ticket:
        return None

    if payment is None:
        payment = Payment.objects.filter(ticket=ticket).order_by('-created_at').first()

    if payment:
        payment.provider = provider
        payment.provider_payment_id = provider_payment_id or payment.provider_payment_id
        payment.status = 'success'
        if payload is not None:
            payment.data = payload
        payment.save()
    else:
        payment = Payment.objects.create(
            ticket=ticket,
            user=ticket.user if hasattr(ticket, 'user') else None,
            provider=provider,
            provider_payment_id=provider_payment_id or '',
            amount=(float(ticket.total_price) if getattr(ticket, 'total_price', None) is not None else 0.0),
            currency=(getattr(ticket, 'currency', None) or 'UAH'),
            status='success',
            data=payload or {}
        )

    ticket.paid = True
    ticket.save(update_fields=['paid'])
    try:
        _send_ticket_email(ticket, payment)
    except Exception:
        pass
    try:
        if request is not None:
            request.session.pop('last_ticket_id', None)
    except Exception:
        pass
    return payment


def _render_wayforpay_form(request, ticket, payment, contact_email='', contact_phone=''):
    order_reference = f"ticket-{ticket.id}"
    amount_value = f"{Decimal(str(payment.amount or 0)).quantize(Decimal('0.01')):.2f}"
    merchant_login = getattr(settings, 'WAYFORPAY_MERCHANT_LOGIN', None) or ''
    try:
        merchant_domain = getattr(settings, 'WAYFORPAY_DOMAIN', None) or request.get_host().split(':')[0] or 'localhost'
    except Exception:
        merchant_domain = getattr(settings, 'WAYFORPAY_DOMAIN', None) or 'localhost'
    return_url = getattr(settings, 'WAYFORPAY_RETURN_URL', None) or request.build_absolute_uri(reverse('main:payment_success'))
    service_url = getattr(settings, 'WAYFORPAY_CALLBACK_URL', None) or request.build_absolute_uri(reverse('main:wayforpay_callback'))
    product_names = [f"Квиток {ticket.from_city} → {ticket.to_city}"]
    product_counts = ['1']
    product_prices = [amount_value]

    try:
        logger = logging.getLogger('main')
        logger.info('WayForPay checkout: ticket=%s amount=%s currency=%s order_reference=%s', ticket.id, payment.amount, payment.currency, order_reference)
    except Exception:
        pass

    try:
        service = get_wayforpay_service(
            merchant_login=merchant_login,
            merchant_domain=merchant_domain,
            return_url=return_url,
            callback_url=service_url,
        )
        request_user = getattr(request, 'user', None)
        invoice_payload = service.create_invoice(
            order_reference=order_reference,
            amount=amount_value,
            currency=(payment.currency or 'UAH').upper(),
            product_names=product_names,
            product_counts=product_counts,
            product_prices=product_prices,
            merchant_login=merchant_login,
            merchant_domain=merchant_domain,
            return_url=return_url,
            service_url=service_url,
            client_first_name=getattr(request_user, 'first_name', '') or '',
            client_last_name=getattr(request_user, 'last_name', '') or '',
            client_email=contact_email,
            client_phone=contact_phone,
        )
        invoice_url = (invoice_payload or {}).get('invoiceUrl') or (invoice_payload or {}).get('invoice_url') or (invoice_payload or {}).get('url')
        if invoice_url:
            payment.provider_payment_id = (invoice_payload or {}).get('invoiceId') or payment.provider_payment_id or ''
            payment.data = {'invoice': invoice_payload}
            payment.save(update_fields=['provider_payment_id', 'data'])
            return redirect(invoice_url)

        # If the API response doesn't include a redirect URL, still render a direct
        # WayForPay payment form with the signed payload generated from the service.
        if isinstance(invoice_payload, dict) and invoice_payload.get('merchantAccount') and invoice_payload.get('merchantSignature'):
            payment.provider_payment_id = (invoice_payload or {}).get('invoiceId') or payment.provider_payment_id or ''
            payment.data = {'invoice': invoice_payload}
            payment.save(update_fields=['provider_payment_id', 'data'])
            return render(request, 'wayforpay_form.html', {
                'wayforpay_url': getattr(settings, 'WAYFORPAY_URL', 'https://secure.wayforpay.com/pay'),
                'merchant_login': invoice_payload.get('merchantAccount') or merchant_login,
                'merchant_domain': invoice_payload.get('merchantDomainName') or merchant_domain,
                'merchant_signature': invoice_payload.get('merchantSignature') or invoice_payload.get('merchant_signature') or '',
                'merchant_auth_type': invoice_payload.get('merchantAuthType') or 'SimpleSignature',
                'order_reference': invoice_payload.get('orderReference') or order_reference,
                'order_date': invoice_payload.get('orderDate') or '',
                'amount': invoice_payload.get('amount') or amount_value,
                'currency': invoice_payload.get('currency') or (payment.currency or 'UAH').upper(),
                'product_names': _normalize_wayforpay_list_field(invoice_payload.get('productName')) or product_names,
                'product_counts': _normalize_wayforpay_list_field(invoice_payload.get('productCount')) or product_counts,
                'product_prices': _normalize_wayforpay_list_field(invoice_payload.get('productPrice')) or product_prices,
                'client_first_name': invoice_payload.get('clientFirstName') or getattr(request_user, 'first_name', '') or '',
                'client_last_name': invoice_payload.get('clientLastName') or getattr(request_user, 'last_name', '') or '',
                'client_email': invoice_payload.get('clientEmail') or contact_email,
                'client_phone': invoice_payload.get('clientPhone') or contact_phone,
                'service_url': invoice_payload.get('serviceUrl') or service_url,
                'return_url': invoice_payload.get('returnUrl') or return_url,
                'api_version': invoice_payload.get('apiVersion') or '1',
                'transaction_type': invoice_payload.get('transactionType') or 'CREATE_INVOICE',
            })

        # As a final fallback, build the signed payload locally and render the form.
        direct_payload = None
        if isinstance(invoice_payload, dict) and invoice_payload.get('merchantAccount'):
            direct_payload = invoice_payload

        if not direct_payload or not direct_payload.get('merchantSignature'):
            from .wayforpay import get_wayforpay_service as _get_wayforpay_service_module

            direct_service = _get_wayforpay_service_module(
                merchant_login=merchant_login,
                merchant_domain=merchant_domain,
                return_url=return_url,
                callback_url=service_url,
            )
            request_user = getattr(request, 'user', None)
            direct_payload = direct_service.build_invoice_payload(
                order_reference=order_reference,
                amount=amount_value,
                currency=(payment.currency or 'UAH').upper(),
                product_names=product_names,
                product_counts=product_counts,
                product_prices=product_prices,
                merchant_login=merchant_login,
                merchant_secret=getattr(settings, 'WAYFORPAY_SECRET_KEY', None) or getattr(settings, 'WAYFORPAY_MERCHANT_SECRET', None) or '',
                merchant_domain=merchant_domain,
                return_url=return_url,
                service_url=service_url,
                client_first_name=getattr(request_user, 'first_name', '') or '',
                client_last_name=getattr(request_user, 'last_name', '') or '',
                client_email=contact_email,
                client_phone=contact_phone,
            )

        if not isinstance(direct_payload, dict):
            raise ValueError('Unable to build WayForPay invoice payload')

        payment.provider_payment_id = direct_payload.get('invoiceId') or payment.provider_payment_id or ''
        payment.data = {'invoice': direct_payload}
        payment.save(update_fields=['provider_payment_id', 'data'])
        return render(request, 'wayforpay_form.html', {
            'wayforpay_url': getattr(settings, 'WAYFORPAY_URL', 'https://secure.wayforpay.com/pay'),
            'merchant_login': direct_payload.get('merchantAccount') or merchant_login,
            'merchant_domain': direct_payload.get('merchantDomainName') or merchant_domain,
            'merchant_signature': direct_payload.get('merchantSignature') or '',
            'merchant_auth_type': direct_payload.get('merchantAuthType') or 'SimpleSignature',
            'order_reference': direct_payload.get('orderReference') or order_reference,
            'order_date': direct_payload.get('orderDate') or '',
            'amount': direct_payload.get('amount') or amount_value,
            'currency': direct_payload.get('currency') or (payment.currency or 'UAH').upper(),
            'product_names': _normalize_wayforpay_list_field(direct_payload.get('productName')) or product_names,
            'product_counts': _normalize_wayforpay_list_field(direct_payload.get('productCount')) or product_counts,
            'product_prices': _normalize_wayforpay_list_field(direct_payload.get('productPrice')) or product_prices,
            'client_first_name': direct_payload.get('clientFirstName') or getattr(request_user, 'first_name', '') or '',
            'client_last_name': direct_payload.get('clientLastName') or getattr(request_user, 'last_name', '') or '',
            'client_email': direct_payload.get('clientEmail') or contact_email,
            'client_phone': direct_payload.get('clientPhone') or contact_phone,
            'service_url': direct_payload.get('serviceUrl') or service_url,
            'return_url': direct_payload.get('returnUrl') or return_url,
            'api_version': direct_payload.get('apiVersion') or '1',
            'transaction_type': direct_payload.get('transactionType') or 'CREATE_INVOICE',
        })
    except Exception as exc:
        logger = logging.getLogger('main')
        logger.exception('WayForPay invoice creation failed for ticket %s', getattr(ticket, 'id', None))

    return HttpResponse('WayForPay invoice creation failed', status=502)


def _delete_failed_ticket(ticket):
    try:
        if ticket:
            ticket.delete()
            return True
    except Exception:
        pass
    return False


def _apply_wayforpay_result(ticket, payment, request, payload, success, transaction_id=''):
    from .models import Payment

    if not ticket:
        return None

    if payment is None:
        payment = Payment.objects.filter(ticket=ticket).order_by('-created_at').first()

    if success:
        if payment is None:
            payment = Payment.objects.create(
                ticket=ticket,
                user=ticket.user if hasattr(ticket, 'user') else None,
                provider='wayforpay',
                provider_payment_id=transaction_id or '',
                amount=(float(ticket.total_price) if getattr(ticket, 'total_price', None) is not None else 0.0),
                currency=(getattr(ticket, 'currency', None) or 'UAH'),
                status='success',
                data=payload or {}
            )
        else:
            payment.provider = 'wayforpay'
            payment.provider_payment_id = transaction_id or payment.provider_payment_id or ''
            payment.status = 'success'
            payment.data = payload or {}
            payment.save()
        ticket.paid = True
        ticket.save(update_fields=['paid'])
        try:
            _send_ticket_email(ticket, payment)
        except Exception:
            pass
        return payment

    if payment is None:
        payment = Payment.objects.create(
            ticket=ticket,
            user=ticket.user if hasattr(ticket, 'user') else None,
            provider='wayforpay',
            provider_payment_id=transaction_id or '',
            amount=(float(ticket.total_price) if getattr(ticket, 'total_price', None) is not None else 0.0),
            currency=(getattr(ticket, 'currency', None) or 'UAH'),
            status='failure',
            data=payload or {}
        )
    else:
        payment.provider = 'wayforpay'
        payment.provider_payment_id = transaction_id or payment.provider_payment_id or ''
        payment.status = 'failure'
        payment.data = payload or {}
        payment.save()

    ticket.paid = False
    ticket.save(update_fields=['paid'])
    return payment


def _support_user_allowed(user, require_worker=False):
    """Return True if the given user should be allowed to access support/admin endpoints.

    By default this accepts staff users. If `require_worker` is True, the user must
    also have a linked `support_worker` record. In addition, usernames or emails
    listed in `settings.SUPPORT_ADMINS` are allowed regardless of staff flag.
    """
    try:
        if not user or not getattr(user, 'is_authenticated', False):
            return False
        if getattr(user, 'is_superuser', False):
            return True
        if getattr(user, 'is_staff', False) and (not require_worker or hasattr(user, 'support_worker')):
            return True
        admins = getattr(settings, 'SUPPORT_ADMINS', []) or []
        if admins:
            uname = (getattr(user, 'username', '') or '').strip().lower()
            email = (getattr(user, 'email', '') or '').strip().lower()
            for a in admins:
                a = (a or '').strip().lower()
                if not a:
                    continue
                if a == uname or a == email:
                    return True
    except Exception:
        pass
    return False


def _qr_png_bytes(data, size=220):
    """Return PNG bytes of a QR code for the given data using reportlab.

    Returns None if reportlab graphics are unavailable.
    """
    try:
        from reportlab.graphics.barcode.qr import QrCodeWidget
        from reportlab.graphics.shapes import Drawing
        from reportlab.graphics import renderPM
    except Exception:
        return None

    try:
        qr = QrCodeWidget(data)
        bounds = qr.getBounds()
        qr_w = bounds[2] - bounds[0]
        qr_h = bounds[3] - bounds[1]
        d = Drawing(size, size)
        d.add(qr)
        png = renderPM.drawToString(d, fmt='PNG')
        return png
    except Exception:
        return None


def find_trip_fare(trip, from_city, to_city):
    """Resolve an explicit TripFare for a pair of cities, taking into
    account route-level `include_subcities`. If `include_subcities` is True,
    this will attempt parent-city fallbacks so fares defined for a main city
    are applied to its subcities.
    Returns a TripFare instance or None.
    """
    try:
        if not trip or not from_city or not to_city:
            return None

        # try exact match first
        try:
            fare = trip.fares.filter(from_city=from_city, to_city=to_city).first()
        except Exception:
            fare = TripFare.objects.filter(trip=trip, from_city=from_city, to_city=to_city).first()
        if fare and getattr(fare, 'price', None) is not None and float(fare.price or 0) > 0:
            return fare

        include = getattr(getattr(trip, 'route', None), 'include_subcities', False)
        if not include:
            return None

        # build parent chain (self, parent, grandparent...)
        def hierarchy(c):
            res = []
            cur = c
            seen = set()
            while cur and getattr(cur, 'id', None) and cur.id not in seen:
                res.append(cur)
                seen.add(cur.id)
                cur = getattr(cur, 'parent', None)
            return res

        from_cands = hierarchy(from_city)
        to_cands = hierarchy(to_city)
        for fc in from_cands:
            for tc in to_cands:
                try:
                    f = trip.fares.filter(from_city=fc, to_city=tc).first()
                except Exception:
                    f = TripFare.objects.filter(trip=trip, from_city=fc, to_city=tc).first()
                if f and getattr(f, 'price', None) is not None and float(f.price or 0) > 0:
                    return f
    except Exception:
        return None
    return None
    try:
        qr = QrCodeWidget(data)
        bounds = qr.getBounds()
        qr_w = bounds[2] - bounds[0]
        qr_h = bounds[3] - bounds[1]
        # create drawing scaled to requested size
        d = Drawing(size, size)
        d.add(qr)
        png = renderPM.drawToString(d, fmt='PNG')
        return png
    except Exception:
        return None




from .models import EmailVerification
from django.http import JsonResponse
from .models import City, Route, RouteStop, Trip, TripStop, TripDayAvailability, TripFare
from django.db.models import Q, Exists, OuterRef, Count, Sum
from .models import StaticPage, SiteConfig, PasswordChangeRequest
from django.db.models import Count
from django.contrib.auth.hashers import make_password
from django.contrib.auth import update_session_auth_hash
import datetime
from .models import StaticPage, SiteConfig


# ========================
# СТАТИЧНІ СТОРІНКИ
# ========================

def home(request):
    # show "hot trips" on homepage: trips ordered by number of sold tickets
    try:
        today = datetime.date.today()
        hot_trips = (
            Trip.objects.filter(active=True)
            .annotate(sold=Count('tickets'))
            .select_related('start_city', 'end_city', 'route')
            .order_by('-sold', 'date')[:8]
        )
    except Exception:
        hot_trips = []
    try:
        presets = SupportPresetQuestion.objects.all().order_by('order')
    except Exception:
        presets = []

    # provide a quick support ticket form on the homepage
    try:
        support_form = SupportTicketForm()
    except Exception:
        support_form = None

    return render(request, 'index.html', {'hot_trips': hot_trips, 'presets': presets, 'support_form': support_form})


def about(request):
    return render(request, 'about.html')


def bova(request):
    return render(request, 'bova.html')


def eos(request):
    return render(request, 'eos.html')


def mercedes2(request):
    return render(request, 'mercedes2.html')


def nashbusindex(request):
    try:
        buses = Bus.objects.all().order_by('title')
    except Exception:
        buses = []
    return render(request, 'nashbusindex.html', {'buses': buses})


def neolplanwhite(request):
    return render(request, 'neolplanwhite.html')


def neoplanred(request):
    return render(request, 'Neoplanred.html')


def cities_table(request):
    try:
        cities = City.objects.all().order_by('name')
    except Exception:
        cities = []
    return render(request, 'cities_table.html', {'cities': cities})


def static_page(request, slug):
    """Render a DB-managed static page by slug.

    Pages are editable via admin and intended for legal/content pages
    such as refund policy or technical FAQ.
    """
    try:
        page = StaticPage.objects.filter(slug=slug, is_published=True).first()
    except Exception:
        page = None
    if not page:
        return HttpResponse(status=404)
    # Render content as safe HTML (admin-provided)
    return render(request, 'static_page.html', {'page': page})


def set_language(request):
    """Simple language switcher that stores choice in session and cookie.

    Keeps interface localized for users; content pages must be created
    per-language in the admin.
    """
    lang = request.GET.get('language') or request.POST.get('language')
    allowed = ('uk', 'en')
    if not lang or lang not in allowed:
        # default to Ukrainian
        lang = 'uk'
    try:
        request.session['django_language'] = lang
    except Exception:
        pass
    # set cookie for persistence
    redirect_to = request.META.get('HTTP_REFERER', '/')
    response = redirect(redirect_to)
    try:
        response.set_cookie('django_language', lang, max_age=30 * 24 * 3600)
    except Exception:
        pass
    return response


def agreements(request):
    """Render the public agreement page (static HTML template)."""
    return render(request, 'agreements.html')


def privacy(request):
    """Render site Privacy Policy page (static content).

    The template `privacy.html` contains the full privacy policy text.
    """
    return render(request, 'privacy.html')


def offer_agent(request):
    """Render agent public offer page (static content).

    The template `offer_agent.html` contains the public offer text for the booking agent.
    """
    return render(request, 'offer_agent.html')


def refunds(request):
    """Render refunds policy page."""
    return render(request, 'refunds.html')


# ========================
# Password change via email verification
# ========================


@login_required
def request_password_change(request):
    if request.method == 'POST':
        form = RequestPasswordChangeForm(request.POST)
        if form.is_valid():
            new_password = form.cleaned_data['new_password']
            code = str(random.randint(100000, 999999))
            hashed = make_password(new_password)
            expires_at = timezone.now() + datetime.timedelta(hours=1)
            PasswordChangeRequest.objects.update_or_create(
                user=request.user,
                defaults={
                    'code': code,
                    'password_hash': hashed,
                    'expires_at': expires_at,
                    'used': False,
                }
            )

            # send verification email
            try:
                html = render_to_string('emails/verification_email.html', {'code': code})
                msg = EmailMultiAlternatives(
                    "Підтвердження зміни пароля — Dieller Bus",
                    f"Ваш код підтвердження: {code}",
                    settings.DEFAULT_FROM_EMAIL,
                    [request.user.email],
                )
                msg.attach_alternative(html, "text/html")
                msg.send(fail_silently=False)
            except Exception:
                try:
                    send_mail(
                        "Підтвердження зміни пароля — Dieller Bus",
                        f"Ваш код підтвердження: {code}",
                        getattr(settings, 'DEFAULT_FROM_EMAIL', None),
                        [request.user.email],
                        fail_silently=True,
                    )
                except Exception:
                    pass

            request.session['password_change_user_id'] = request.user.id
            return redirect('main:verify_password_change')
    else:
        form = RequestPasswordChangeForm()
    return render(request, 'request_password_change.html', {'form': form})


@login_required
def verify_password_change(request):
    user_id = request.session.get('password_change_user_id')
    if not user_id or user_id != request.user.id:
        return redirect('main:profile')

    try:
        req = PasswordChangeRequest.objects.get(user=request.user, used=False)
    except PasswordChangeRequest.DoesNotExist:
        return redirect('main:request_password_change')

    if request.method == 'POST':
        code = request.POST.get('code')
        if code and req.code == code and req.is_valid():
            user = request.user
            user.password = req.password_hash
            user.save(update_fields=['password'])
            req.used = True
            req.save(update_fields=['used'])
            update_session_auth_hash(request, user)
            try:
                del request.session['password_change_user_id']
            except Exception:
                pass
            return redirect('main:profile')

        return render(request, 'verify_password_change.html', {
            'error': 'Невірний код',
            'email': request.user.email,
        })

    return render(request, 'verify_password_change.html', {'email': request.user.email})


@login_required
@require_POST
def resend_password_change_code(request):
    user_id = request.session.get('password_change_user_id')
    if not user_id or user_id != request.user.id:
        return JsonResponse({"ok": False, "message": "Сесія не знайдена"}, status=400)

    try:
        req = PasswordChangeRequest.objects.get(user=request.user, used=False)
    except PasswordChangeRequest.DoesNotExist:
        return JsonResponse({"ok": False, "message": "Немає активного запиту"}, status=400)

    try:
        code = str(random.randint(100000, 999999))
        req.code = code
        req.expires_at = timezone.now() + datetime.timedelta(hours=1)
        req.save(update_fields=['code', 'expires_at'])

        try:
            html = render_to_string('emails/verification_email.html', {'code': code})
            msg = EmailMultiAlternatives(
                "Підтвердження зміни пароля — Dieller Bus",
                f"Ваш код підтвердження: {code}",
                settings.DEFAULT_FROM_EMAIL,
                [request.user.email],
            )
            msg.attach_alternative(html, "text/html")
            msg.send(fail_silently=False)
        except Exception:
            try:
                send_mail(
                    "Підтвердження зміни пароля — Dieller Bus",
                    f"Ваш код підтвердження: {code}",
                    getattr(settings, 'DEFAULT_FROM_EMAIL', None),
                    [request.user.email],
                    fail_silently=True,
                )
            except Exception:
                pass

        return JsonResponse({"ok": True, "message": "Код надіслано"})
    except Exception:
        return JsonResponse({"ok": False, "message": "Сталася помилка"}, status=500)


# ========================
# Password reset (anonymous) using 6-digit code stored in session
# ========================

def password_reset_request(request):
    """Anonymous: submit email, server sends 6-digit code and stores it in session.

    This flow keeps state in the user's session (no new DB model) so the user
    must use the same browser to complete the reset.
    """
    from django.contrib import messages as _messages
    import datetime as _dt
    logger = logging.getLogger(__name__)

    if request.method == 'POST':
        email = (request.POST.get('email') or '').strip()
        if not email:
            return render(request, 'registration/password_reset_form.html', {'error': 'Введіть email'})

        user = User.objects.filter(email__iexact=email).first()
        if not user:
            # For privacy, don't reveal whether account exists — show same UI path
            # but log an info entry so admins can inspect if needed.
            logger.info('Password reset requested for unknown email: %s', email)
            # still create a minimal session marker so user sees the verify page
            request.session['password_reset'] = {'user_id': None, 'code': None, 'expires_at': None}
            return redirect('main:password_reset_verify')

        code = str(random.randint(100000, 999999))
        expires_ts = timezone.now().timestamp() + 3600  # 1 hour
        request.session['password_reset'] = {
            'user_id': user.id,
            'code': code,
            'expires_at': int(expires_ts),
        }

        sent = False
        try:
            html = render_to_string('emails/verification_email.html', {'code': code})
            msg = EmailMultiAlternatives(
                "Код для відновлення пароля — Dieller Bus",
                f"Ваш код для відновлення пароля: {code}",
                settings.DEFAULT_FROM_EMAIL,
                [user.email],
            )
            msg.attach_alternative(html, "text/html")
            msg.send(fail_silently=False)
            sent = True
        except Exception as exc:
            logger.exception('Failed to send password reset email (primary): %s', exc)
            try:
                send_mail(
                    "Код для відновлення пароля — Dieller Bus",
                    f"Ваш код для відновлення пароля: {code}",
                    getattr(settings, 'DEFAULT_FROM_EMAIL', None),
                    [user.email],
                    fail_silently=False,
                )
                sent = True
            except Exception as exc2:
                logger.exception('Fallback password reset email failed: %s', exc2)

        if not sent:
            # If sending failed, show a warning but still redirect so user can
            # enter the one-time code (if they received it) without losing flow.
            _messages.warning(request, 'Не вдалося надіслати код електронною поштою. Якщо ви не отримаєте листа, зверніться в підтримку.')
        else:
            _messages.success(request, 'Ми надіслали 6-значний код на вказану адресу. Перевірте також папку "Спам".')

        return redirect('main:password_reset_verify')

    return render(request, 'registration/password_reset_form.html')


def password_reset_verify(request):
    """Page where user enters 6-digit code and new password.

    Uses session-stored code created by `password_reset_request`.
    """
    from django.contrib import messages as _messages
    import datetime as _dt
    logger = logging.getLogger(__name__)

    pr = request.session.get('password_reset')
    if not pr:
        return redirect('main:password_reset')

    user = None
    try:
        if pr.get('user_id'):
            user = User.objects.get(id=pr.get('user_id'))
    except User.DoesNotExist:
        user = None

    email = user.email if user else ''

    if request.method == 'POST':
        code = (request.POST.get('code') or '').strip()
        new1 = request.POST.get('new_password1') or ''
        new2 = request.POST.get('new_password2') or ''

        # validate session and expiry
        try:
            expires_at = int(pr.get('expires_at') or 0)
            if expires_at and timezone.now().timestamp() > expires_at:
                request.session.pop('password_reset', None)
                return render(request, 'registration/password_reset_form.html', {'error': 'Термін дії коду минув. Запросіть новий код.'})
        except Exception:
            pass

        if not code or not pr.get('code') or code != pr.get('code'):
            return render(request, 'registration/password_reset_verify.html', {'error': 'Невірний код', 'email': email})

        if not new1 or new1 != new2:
            return render(request, 'registration/password_reset_verify.html', {'error': 'Паролі не співпадають', 'email': email})

        if not user:
            return render(request, 'registration/password_reset_verify.html', {'error': 'Користувача не знайдено', 'email': email})

        try:
            user.set_password(new1)
            user.save(update_fields=['password'])
            request.session.pop('password_reset', None)
            auth_login(request, user)
            return redirect('main:profile')
        except Exception as exc:
            logger.exception('Failed to set new password: %s', exc)
            _messages.error(request, 'Не вдалося змінити пароль — спробуйте знову пізніше')
            return render(request, 'registration/password_reset_verify.html', {'email': email})

    return render(request, 'registration/password_reset_verify.html', {'email': email})


@require_POST
def resend_password_reset_code(request):
    logger = logging.getLogger(__name__)
    pr = request.session.get('password_reset')
    if not pr or not pr.get('user_id'):
        return JsonResponse({'ok': False, 'message': 'Сесія не знайдена'}, status=400)

    try:
        user = User.objects.get(id=pr.get('user_id'))
    except User.DoesNotExist:
        return JsonResponse({'ok': False, 'message': 'Користувача не знайдено'}, status=400)

    code = str(random.randint(100000, 999999))
    pr['code'] = code
    pr['expires_at'] = int(timezone.now().timestamp() + 3600)
    request.session['password_reset'] = pr

    sent = False
    try:
        html = render_to_string('emails/verification_email.html', {'code': code})
        msg = EmailMultiAlternatives(
            "Код для відновлення пароля — Dieller Bus",
            f"Ваш код для відновлення пароля: {code}",
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
        )
        msg.attach_alternative(html, "text/html")
        msg.send(fail_silently=False)
        sent = True
    except Exception as exc:
        logger.exception('Failed to resend password reset email (primary): %s', exc)
        try:
            send_mail(
                "Код для відновлення пароля — Dieller Bus",
                f"Ваш код для відновлення пароля: {code}",
                getattr(settings, 'DEFAULT_FROM_EMAIL', None),
                [user.email],
                fail_silently=False,
            )
            sent = True
        except Exception as exc2:
            logger.exception('Fallback resend failed: %s', exc2)

    if not sent:
        return JsonResponse({'ok': False, 'message': 'Не вдалося надіслати код'}, status=500)
    return JsonResponse({'ok': True, 'message': f'Код надіслано на {user.email}'})
@login_required
def support_home(request):
    presets = SupportPresetQuestion.objects.all().order_by('order')
    if not presets.exists():
        defaults = [
            ("Не прийшов квиток після покупки?", "Квиток не прийшов на email або в особистий кабінет після оплати"),
            ("Не можете купити квиток?", "Виникає помилка при оформленні або обробці платежу"),
            ("Проблема з оплатою", "Платіж пройшов або не пройшов — перевірка статусу платежу"),
            ("Повернення або зміна броні", "Хочу повернути квиток або змінити дату/пасажирів"),
            ("Інші питання", "Опис вашої проблеми або запитання"),
        ]
        for i, (title, desc) in enumerate(defaults, start=1):
            SupportPresetQuestion.objects.create(title=title, description=desc, order=i)
        presets = SupportPresetQuestion.objects.all().order_by('order')

    # ensure explicit refund preset exists for clarity
    if not SupportPresetQuestion.objects.filter(title__icontains='Повернення квитка').exists():
        SupportPresetQuestion.objects.create(title='Повернення квитка', description='Запит на повернення коштів за квиток — вкажіть номер квитка у формі', order=(presets.count() + 1))
        presets = SupportPresetQuestion.objects.all().order_by('order')

    tickets = SupportTicket.objects.filter(user=request.user, is_archived=False).order_by('-last_message_at', '-created_at')

    if request.method == 'POST':
        form = SupportTicketForm(request.POST)
        initial_message = request.POST.get('initial_message', '').strip()
        initial_attachment = request.FILES.get('initial_attachment')
        if form.is_valid() and initial_message:
            ticket = form.save(commit=False)
            ticket.user = request.user
            ticket.save()
            SupportMessage.objects.create(ticket=ticket, sender=request.user, text=initial_message, attachment=initial_attachment, is_from_admin=False)
            try:
                request.session['support_popup'] = True
            except Exception:
                pass
            return redirect('main:support_ticket_detail', ticket.id)
    else:
        form = SupportTicketForm()

    return render(request, 'support.html', {'presets': presets, 'tickets': tickets, 'form': form})


@login_required
@require_POST
def ask_question(request):
    """Quick endpoint for submitting a support/question ticket from the homepage.

    Requires authentication. Accepts POST fields: 'preset' (id), 'subject', 'initial_message',
    and optional file 'initial_attachment'. Creates SupportTicket + first SupportMessage.
    """
    form = SupportTicketForm(request.POST)
    initial_message = (request.POST.get('initial_message') or '').strip()
    initial_attachment = request.FILES.get('initial_attachment')
    if form.is_valid() and initial_message:
        ticket = form.save(commit=False)
        ticket.user = request.user
        ticket.save()
        try:
            SupportMessage.objects.create(ticket=ticket, sender=request.user, text=initial_message, attachment=initial_attachment, is_from_admin=False)
        except Exception:
            # even if message creation fails, proceed
            pass
        messages.success(request, 'Питання надіслано. Ми відповімо в особистому кабінеті.')
        return redirect('main:support_ticket_detail', ticket.id)

    messages.error(request, 'Будь ласка, опишіть ваше питання.')
    return redirect('main:home')


@login_required
def support_worker_queue(request):
    # Only allow users flagged as staff/support workers (or explicitly allowed admins)
    if not _support_user_allowed(request.user, require_worker=True):
        return HttpResponse(status=403)

    qs = SupportTicket.objects.filter(is_archived=False).order_by('status', '-last_message_at', 'created_at')
    # counts
    counts = {
        'new': SupportTicket.objects.filter(status=SupportTicket.STATUS_NEW, is_archived=False).count(),
        'in_progress': SupportTicket.objects.filter(status=SupportTicket.STATUS_IN_PROGRESS, is_archived=False).count(),
        'closed': SupportTicket.objects.filter(status=SupportTicket.STATUS_CLOSED, is_archived=False).count(),
    }
    # tickets assigned to current worker
    my_tickets = SupportTicket.objects.filter(assigned_to=request.user, is_archived=False).order_by('-last_message_at')
    return render(request, 'support_worker_queue.html', {'tickets': qs, 'counts': counts, 'my_tickets': my_tickets})


@login_required
def support_worker_take(request, ticket_id):
    if not _support_user_allowed(request.user, require_worker=True):
        return HttpResponse(status=403)
    ticket = get_object_or_404(SupportTicket, pk=ticket_id)
    ticket.assigned_to = request.user
    ticket.status = SupportTicket.STATUS_IN_PROGRESS
    ticket.save()
    # notify user in chat that operator accepted
    try:
        SupportMessage.objects.create(ticket=ticket, sender=request.user, text=f'Працівник {request.user.username} приєднався до чату.', is_from_admin=True)
    except Exception:
        pass
    return redirect('main:support_ticket_detail', ticket.id)


@login_required
def support_admin_list(request):
    if not _support_user_allowed(request.user):
        return HttpResponse(status=403)

    qs = SupportTicket.objects.filter(is_archived=False).order_by('-last_message_at', '-created_at')
    # allow quick filtering by status from querystring (buttons in template)
    status = request.GET.get('status')
    if status in (SupportTicket.STATUS_NEW, SupportTicket.STATUS_IN_PROGRESS, SupportTicket.STATUS_CLOSED):
        qs = qs.filter(status=status)

    counts = {
        'new': SupportTicket.objects.filter(status=SupportTicket.STATUS_NEW, is_archived=False).count(),
        'in_progress': SupportTicket.objects.filter(status=SupportTicket.STATUS_IN_PROGRESS, is_archived=False).count(),
        'closed': SupportTicket.objects.filter(status=SupportTicket.STATUS_CLOSED, is_archived=False).count(),
    }
    return render(request, 'support_admin.html', {'tickets': qs, 'counts': counts, 'selected_status': status})


@login_required
def support_admin_cancel_trip(request):
    """Staff view: choose a Trip and a date to mark as cancelled and notify buyers.

    Marks `TripDayAvailability.available=False` for the selected date and sends
    an email to every paid ticket for that trip+date with links to rebook/refund.
    """
    if not _support_user_allowed(request.user):
        return HttpResponse(status=403)

    trips = Trip.objects.all().order_by('date', 'id')
    trip_id = request.GET.get('trip_id') or request.POST.get('trip_id')
    selected_trip = None
    dates = []
    if trip_id:
        try:
            selected_trip = Trip.objects.filter(pk=int(trip_id)).first()
        except Exception:
            selected_trip = None

    if selected_trip:
        # collect dates with sold (paid) tickets for this trip and sum passengers
        dates_qs = (
            Ticket.objects.filter(trip=selected_trip, paid=True)
            .values('travel_date')
            .annotate(count=Count('id'), passengers=Sum('passengers'))
        )
        for d in dates_qs:
            dt = d.get('travel_date')
            if not dt:
                continue
            # skip dates already marked unavailable/cancelled
            try:
                if TripDayAvailability.objects.filter(trip=selected_trip, date=dt, available=False).exists():
                    continue
            except Exception:
                pass
            dates.append({'date': dt, 'count': d.get('count') or 0, 'passengers': d.get('passengers') or 0})

        # optionally include the Trip.date if set (and not already present)
        try:
            trip_date = getattr(selected_trip, 'date', None)
            if trip_date and not any(x['date'] == trip_date for x in dates):
                agg = Ticket.objects.filter(trip=selected_trip, travel_date=trip_date, paid=True).aggregate(count=Count('id'), passengers=Sum('passengers'))
                dates.append({'date': trip_date, 'count': agg.get('count') or 0, 'passengers': agg.get('passengers') or 0})
        except Exception:
            pass

        # keep only future (or today) dates and sort ascending
        try:
            today = timezone.now().date()
            dates = [d for d in dates if d.get('date') and d['date'] >= today]
            dates.sort(key=lambda x: x['date'])
        except Exception:
            # fallback: preserve original order if filtering fails
            pass

    if request.method == 'POST':
        # perform cancellation: mark date unavailable, refund users, debit carrier, and notify
        # log incoming POST for debugging when UI button appears to do nothing
        try:
            logging.info('support_admin_cancel_trip POST by user=%s id=%s POST_keys=%s', getattr(request.user, 'username', None), getattr(request.user, 'id', None), list(request.POST.keys()))
        except Exception:
            logging.exception('Failed to log support_admin_cancel_trip POST metadata')

        try:
            trip_pk = int(request.POST.get('trip_id'))
            date_str = (request.POST.get('date') or '').strip()
            # accept YYYY-MM-DD or DD.MM.YYYY
            try:
                cancel_date = datetime.date.fromisoformat(date_str)
            except Exception:
                try:
                    cancel_date = datetime.datetime.strptime(date_str, '%d.%m.%Y').date()
                except Exception:
                    cancel_date = None

            trip = Trip.objects.filter(pk=trip_pk).first()
            if not trip or not cancel_date:
                messages.error(request, 'Невірні параметри')
                return redirect('main:support_admin_cancel_trip')

            # collect tickets first
            tickets_qs = Ticket.objects.filter(trip=trip, travel_date=cancel_date, paid=True).select_related('user')
            tickets = list(tickets_qs)
            try:
                logging.info('support_admin_cancel_trip found %s tickets for trip_id=%s date=%s', len(tickets), getattr(trip, 'id', None), str(cancel_date))
            except Exception:
                logging.exception('Failed to log tickets info in support_admin_cancel_trip')

            total_tickets = len(tickets)
            total_passengers = sum([int(getattr(t, 'passengers', 0) or 0) for t in tickets])

            sent = 0
            # perform DB changes in a transaction so availability + refunds are consistent
            try:
                with transaction.atomic():
                    TripDayAvailability.objects.update_or_create(trip=trip, date=cancel_date, defaults={'available': False})

                    for t in tickets:
                        try:
                            refund_amount = float(t.total_price or 0.0)
                        except Exception:
                            refund_amount = 0.0

                        # Ledger: create negative payment (manual refund) for the user
                        try:
                            Payment.objects.create(ticket=t, user=t.user, amount=-abs(refund_amount), currency=(t.currency or 'UAH'), status='refunded', provider='manual_refund', data={'by': request.user.username if request.user.is_authenticated else 'system', 'ticket_id': t.id, 'reason': 'trip_cancelled'})
                        except Exception:
                            logging.exception('Failed to create refund payment for ticket %s', getattr(t, 'id', None))

                        # credit user's balance so they can be refunded immediately
                        try:
                            profile = getattr(getattr(t, 'user', None), 'profile', None)
                            if profile:
                                profile.balance = (profile.balance or 0) + refund_amount
                                profile.save(update_fields=['balance'])
                        except Exception:
                            logging.exception('Failed to credit user balance for ticket %s', getattr(t, 'id', None))

                        # mark ticket as unpaid so it no longer appears as active
                        try:
                            t.paid = False
                            t.save(update_fields=['paid'])
                        except Exception:
                            logging.exception('Failed to mark ticket unpaid %s', getattr(t, 'id', None))

                        # debit carrier profile balance if linked
                        try:
                            if getattr(trip, 'carrier_user', None):
                                carrier_profile = getattr(trip.carrier_user, 'profile', None)
                                if carrier_profile:
                                    carrier_profile.balance = (carrier_profile.balance or 0) - refund_amount
                                    carrier_profile.save(update_fields=['balance'])
                                    Payment.objects.create(ticket=None, user=trip.carrier_user, amount=-abs(refund_amount), currency=(t.currency or 'UAH'), status='refunded', provider='carrier_refund', data={'ticket_id': t.id, 'by': request.user.username if request.user.is_authenticated else 'system', 'reason': 'trip_cancelled'})
                        except Exception:
                            logging.exception('Failed to debit carrier for ticket %s', getattr(t, 'id', None))
            except Exception:
                logging.exception('Failed to update availability and refunds atomically')

            # send notification emails (do not rollback DB changes on email failure)
            for t in tickets:
                try:
                    sig = _ticket_signature(t)
                    base = request.build_absolute_uri(reverse('main:cancellation_manage', args=[t.id]))
                    cancel_url = f"{base}?sig={sig}"
                    rebook_url = request.build_absolute_uri(reverse('main:kvitokindex')) + f"?exchange_ticket={t.id}"
                    refund_url = f"{cancel_url}&action=refund"
                    subject = f"Скасовано рейс {t.from_city} → {t.to_city} — {cancel_date.strftime('%d.%m.%Y')}"
                    html = render_to_string('emails/cancellation_email.html', {'ticket': t, 'cancel_url': cancel_url, 'rebook_url': rebook_url, 'refund_url': refund_url, 'trip': trip, 'date': cancel_date})
                    msg = EmailMessage(subject, html, settings.DEFAULT_FROM_EMAIL, [t.contact_email or (t.user.email if getattr(t, 'user', None) else None)])
                    msg.content_subtype = 'html'
                    try:
                        res = msg.send(fail_silently=False)
                        if isinstance(res, int):
                            sent += res
                    except Exception:
                        logging.exception('Failed to send cancellation email for ticket %s', getattr(t, 'id', None))
                        continue
                except Exception:
                    logging.exception('Failed to build cancellation email for ticket %s', getattr(t, 'id', None))
                    continue

            # save a summary to session so the page can display details after redirect
            try:
                request.session['last_cancellation'] = {
                    'trip_id': trip_pk,
                    'date': cancel_date.isoformat(),
                    'tickets': int(total_tickets),
                    'passengers': int(total_passengers),
                    'notified': int(sent),
                }
            except Exception:
                pass

            messages.info(request, f'Рейс позначено як скасований на {cancel_date}. Квитків: {total_tickets}. Повідомлень надіслано: {sent}.')
            return redirect(f"{reverse('main:support_admin_cancel_trip')}?trip_id={trip_pk}")
        except Exception as ex:
            logging.exception('Unexpected error during trip cancellation')
            # preserve a small debug summary in session for staff to inspect after redirect
            try:
                request.session['last_cancellation'] = {
                    'trip_id': int(request.POST.get('trip_id')) if request.POST.get('trip_id') else None,
                    'date': (request.POST.get('date') or '').strip(),
                    'error': str(ex)[:1000],
                }
            except Exception:
                logging.exception('Failed to write last_cancellation debug info to session')
            messages.error(request, 'Сталася помилка при скасуванні (деталі доступні для адмінів)')
            return redirect('main:support_admin_cancel_trip')

    # pop any recent cancellation summary so we can display it once in the template
    cancellation_summary = None
    try:
        cancellation_summary = request.session.pop('last_cancellation', None)
    except Exception:
        cancellation_summary = None

    return render(request, 'support_admin_cancel_trip.html', {
        'trips': trips,
        'selected_trip': selected_trip,
        'dates': dates,
        'cancellation_summary': cancellation_summary,
    })


@login_required
def support_admin_take(request, ticket_id):
    if not _support_user_allowed(request.user):
        return HttpResponse(status=403)
    ticket = get_object_or_404(SupportTicket, pk=ticket_id)
    ticket.assigned_to = request.user
    ticket.status = SupportTicket.STATUS_IN_PROGRESS
    ticket.save()
    try:
        SupportMessage.objects.create(ticket=ticket, sender=request.user, text=f'Адміністратор {request.user.username} приєднався до чату.', is_from_admin=True)
    except Exception:
        pass
    return redirect('main:support_ticket_detail', ticket.id)


@login_required
def support_admin_send_rebook(request, ticket_id):
    if not _support_user_allowed(request.user):
        return HttpResponse(status=403)
    ticket = get_object_or_404(Ticket, pk=ticket_id)
    if request.method != 'POST':
        return HttpResponse(status=405)
    try:
        sig = _ticket_signature(ticket)
        # redirect user to search page with exchange_ticket param
        rebook_url = request.build_absolute_uri(reverse('main:kvitokindex')) + f"?exchange_ticket={ticket.id}"
        subject = f"Пропозиція перебронювання — квиток #{ticket.id}"
        html = render_to_string('emails/rebook_offer.html', {'ticket': ticket, 'rebook_url': rebook_url})
        msg = EmailMessage(subject, html, settings.DEFAULT_FROM_EMAIL, [ticket.contact_email or (ticket.user.email if getattr(ticket, 'user', None) else None)])
        msg.content_subtype = 'html'
        try:
            res = msg.send(fail_silently=False)
        except Exception:
            logging.exception('Failed to send rebook offer for ticket %s', ticket.id)
            raise
        # create support ticket entry for traceability
        preset = SupportPresetQuestion.objects.filter(title__icontains='переброн').first()
        st = SupportTicket.objects.create(user=ticket.user, subject=f'Перебронювання квитка #{ticket.id}', ticket=ticket, preset=preset)
        SupportMessage.objects.create(ticket=st, sender=request.user, text='Надіслано пропозицію перебронювання (ініційовано техпідтримкою).', is_from_admin=True)
        messages.info(request, 'Лист з пропозицією перебронювання надіслано')
    except Exception:
        messages.error(request, 'Не вдалося надіслати лист')
    referer = request.META.get('HTTP_REFERER')
    if referer:
        return redirect(referer)
    return redirect('main:support_admin')


@login_required
def support_admin_attach(request, ticket_id):
    # staff endpoint to attach an existing Ticket to a SupportTicket
    if not _support_user_allowed(request.user):
        return HttpResponse(status=403)
    st = get_object_or_404(SupportTicket, pk=ticket_id)
    if request.method == 'POST':
        attach_id = request.POST.get('attach_ticket_id')
        try:
            attach_id = int(str(attach_id).strip())
        except Exception:
            attach_id = None
        if attach_id:
            t = Ticket.objects.filter(pk=attach_id).first()
            if t:
                st.ticket = t
                st.save()
                messages.info(request, f"Прив'язано квиток #{t.id} до звернення")
                return redirect('main:support_ticket_detail', st.id)
        messages.error(request, 'Квиток не знайдено')
        return redirect('main:support_ticket_detail', st.id)
    return HttpResponse(status=405)


@login_required
def support_admin_close(request, ticket_id):
    if not _support_user_allowed(request.user):
        return HttpResponse(status=403)
    ticket = get_object_or_404(SupportTicket, pk=ticket_id)
    ticket.status = SupportTicket.STATUS_CLOSED
    ticket.is_archived = True
    ticket.save()
    try:
        SupportMessage.objects.create(ticket=ticket, sender=request.user, text=f'Звернення закрите адміністратором {request.user.username}.', is_from_admin=True)
    except Exception:
        pass
    return redirect('main:support_admin')


@login_required
def support_admin_user(request, user_id):
    if not _support_user_allowed(request.user):
        return HttpResponse(status=403)
    user = get_object_or_404(User, pk=user_id)
    tickets = SupportTicket.objects.filter(user=user, is_archived=False).order_by('-last_message_at', '-created_at')
    # Also collect purchased tickets and user profile info for full view
    purchased = Ticket.objects.filter(user=user).order_by('-created_at')
    try:
        profile = user.profile
    except Exception:
        profile = None
    return render(request, 'support_admin_user.html', {'tickets': tickets, 'view_user': user, 'purchased_tickets': purchased, 'profile': profile})


@login_required
def support_resend_ticket(request, ticket_id):
    if not _support_user_allowed(request.user):
        return HttpResponse(status=403)
    ticket = get_object_or_404(Ticket, pk=ticket_id)
    try:
        _send_ticket_email(ticket, None)
        messages.info(request, f'Квиток #{ticket.id} відправлено на {ticket.user.email}')
    except Exception:
        messages.error(request, 'Не вдалося надіслати квиток')
    # redirect back to referring page if available
    referer = request.META.get('HTTP_REFERER')
    if referer:
        return redirect(referer)
    return redirect('main:support_admin_user', ticket.user_id)


@login_required
def support_edit_user(request, user_id):
    if not _support_user_allowed(request.user):
        return HttpResponse(status=403)
    target = get_object_or_404(User, pk=user_id)
    try:
        profile = target.profile
    except Exception:
        profile = None

    if request.method == 'POST':
        user_form = SupportUserForm(request.POST, instance=target)
        profile_form = ProfileForm(request.POST, request.FILES, instance=profile)
        if user_form.is_valid() and profile_form.is_valid():
            user_form.save()
            p = profile_form.save(commit=False)
            p.user = target
            p.save()
            messages.info(request, 'Профіль оновлено')
            return redirect('main:support_admin_user', target.id)
    else:
        user_form = SupportUserForm(instance=target)
        profile_form = ProfileForm(instance=profile)

    return render(request, 'support_admin_user_edit.html', {'view_user': target, 'user_form': user_form, 'profile_form': profile_form})


@login_required
def support_api_my_ticket(request):
    # return user's latest active ticket (JSON) to power popup UI
    user = request.user
    ticket = SupportTicket.objects.filter(user=user, is_archived=False).exclude(status=SupportTicket.STATUS_CLOSED).order_by('-last_message_at', '-created_at').first()
    if not ticket:
        return JsonResponse({'ticket': None})

    # include recent messages to render in the popup
    msgs_qs = ticket.messages.order_by('-created_at')[:10]
    messages_list = []
    for m in reversed(list(msgs_qs)):
        try:
            created = timezone.localtime(m.created_at).strftime('%d.%m.%Y %H:%M')
        except Exception:
            created = str(m.created_at)
        sender_avatar = None
        try:
            if m.sender and getattr(m.sender, 'profile', None) and m.sender.profile.avatar:
                sender_avatar = m.sender.profile.avatar.url
        except Exception:
            sender_avatar = None
        messages_list.append({
            'id': m.id,
            'text': m.text,
            'sender': m.sender.username if m.sender else 'Система',
            'is_from_admin': bool(m.is_from_admin),
            'created_at': created,
            'attachment': (m.attachment.url if m.attachment else None),
            'sender_avatar': sender_avatar,
        })

    try:
        created_at = timezone.localtime(ticket.created_at).strftime('%d.%m.%Y %H:%M') if ticket.created_at else ''
    except Exception:
        created_at = str(ticket.created_at)

    return JsonResponse({'ticket': {
        'id': ticket.id,
        'status': ticket.status,
        'assigned_to': ticket.assigned_to.username if ticket.assigned_to else None,
        'status_display': ticket.get_status_display(),
        'subject': ticket.subject or (ticket.preset.title if ticket.preset else ''),
        'created_at': created_at,
        'messages': messages_list,
    }})


@login_required
def support_api_send_message(request, ticket_id):
    """AJAX endpoint to send a support message and return the saved message JSON."""
    if request.method != 'POST':
        return JsonResponse({'error': 'method_not_allowed'}, status=405)
    ticket = get_object_or_404(SupportTicket, pk=ticket_id)
    if ticket.user != request.user and not request.user.is_staff:
        return JsonResponse({'error': 'forbidden'}, status=403)

    text = (request.POST.get('text') or '').strip()
    attachment = request.FILES.get('attachment')
    if not text and not attachment:
        return JsonResponse({'error': 'empty_message'}, status=400)

    msg = SupportMessage.objects.create(ticket=ticket, sender=request.user, text=text or '', attachment=attachment, is_from_admin=bool(request.user.is_staff))
    try:
        created = timezone.localtime(msg.created_at).strftime('%d.%m.%Y %H:%M')
    except Exception:
        created = str(msg.created_at)

    message_data = {
        'id': msg.id,
        'text': msg.text,
        'sender': msg.sender.username if msg.sender else 'Система',
        'is_from_admin': bool(msg.is_from_admin),
        'created_at': created,
        'attachment': (msg.attachment.url if msg.attachment else None),
        'sender_avatar': (msg.sender.profile.avatar.url if getattr(msg.sender, 'profile', None) and msg.sender.profile.avatar else None),
    }

    return JsonResponse({'ok': True, 'message': message_data, 'ticket': {'id': ticket.id, 'status': ticket.status, 'assigned_to': ticket.assigned_to.username if ticket.assigned_to else None}})


@login_required
def support_close_popup(request):
    try:
        request.session.pop('support_popup', None)
    except Exception:
        pass
    return JsonResponse({'ok': True})


@login_required
def support_ticket_detail(request, ticket_id):
    ticket = get_object_or_404(SupportTicket, pk=ticket_id)
    if ticket.user != request.user and not request.user.is_staff:
        return HttpResponse(status=403)

    if request.method == 'POST':
        form = SupportMessageForm(request.POST, request.FILES)
        if form.is_valid():
            msg = form.save(commit=False)
            msg.ticket = ticket
            msg.sender = request.user
            msg.is_from_admin = request.user.is_staff
            msg.save()
            return redirect('main:support_ticket_detail', ticket.id)
    else:
        form = SupportMessageForm()

    messages = ticket.messages.select_related('sender', 'sender__profile').all()
    try:
        request.session['support_popup'] = True
    except Exception:
        pass

    # determine whether refund action should be available: staff + (linked ticket or user chose refund preset)
    refund_preset = False
    try:
        if getattr(ticket, 'preset', None) and getattr(ticket.preset, 'title', None):
            refund_preset = 'поверн' in (ticket.preset.title or '').lower()
    except Exception:
        refund_preset = False

    can_refund = bool(request.user.is_staff and (getattr(ticket, 'ticket', None) or refund_preset))

    return render(request, 'support_ticket.html', {'ticket': ticket, 'messages': messages, 'form': form, 'can_refund': can_refund})


def api_trips(request):
    """API endpoint: search trips by origin/destination and optional date/direction.

    Query params accepted: `from`, `to`, `dir`/`direction`, `date`/`travel_date`.
    """
    from datetime import datetime

    from_name = request.GET.get('from') or request.GET.get('from_name') or request.POST.get('from') or ''
    to_name = request.GET.get('to') or request.GET.get('to_name') or request.POST.get('to') or ''
    direction = request.GET.get('dir') or request.GET.get('direction') or ''

    results = []

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

    def normalize_country(token):
        token_norm = normalize_text(token)
        if not token_norm:
            return None
        country_map = {
            'ua': 'ua', 'ukraine': 'ua', 'україна': 'ua', 'ukr': 'ua',
            'pl': 'pl', 'poland': 'pl', 'польща': 'pl', 'pol': 'pl',
            'it': 'it', 'italy': 'it', 'італія': 'it',
            'de': 'de', 'germany': 'de', 'німеччина': 'de',
            'fr': 'fr', 'france': 'fr', 'франція': 'fr',
            'es': 'es', 'spain': 'es', 'іспанія': 'es',
            'ro': 'ro', 'romania': 'ro', 'румунія': 'ro',
        }
        if token_norm in country_map:
            return country_map[token_norm]
        if len(token_norm) <= 3:
            return token_norm
        return None

    q_from = normalize_text(from_name)
    q_to = normalize_text(to_name)

    # parse optional `date` param (accept YYYY-MM-DD or DD.MM.YYYY)
    date_str = request.GET.get('date') or request.GET.get('travel_date')
    date_obj = None
    if date_str:
        try:
            date_obj = datetime.fromisoformat(date_str).date()
        except Exception:
            try:
                date_obj = datetime.strptime(date_str, '%d.%m.%Y').date()
            except Exception:
                date_obj = None

    # Build small normalized map of known cities for token -> City resolution.
    try:
        all_cities_list = list(City.objects.all())
        city_norm_map = {normalize_text(c.name): c for c in all_cities_list}
    except Exception:
        all_cities_list = []
        city_norm_map = {}

    def find_match(stops_list, token, purpose='from'):
        token_norm = normalize_text(token)
        if not token_norm:
            return None

        # If token resolves to a known City, prefer matching stops by city id
        city_obj = None
        try:
            city_obj = City.objects.filter(name__iexact=token).first()
        except Exception:
            city_obj = None
        if not city_obj:
            city_obj = city_norm_map.get(token_norm)

        if city_obj:
            cand_ids = {city_obj.id}
            try:
                include_sub = bool(getattr(getattr(trip, 'route', None), 'include_subcities', False))
            except Exception:
                include_sub = False
            subcity_ids = set()
            if include_sub:
                # add parent chain
                cur = city_obj
                while cur and getattr(cur, 'parent', None):
                    cur = cur.parent
                    if cur and getattr(cur, 'id', None):
                        subcity_ids.add(cur.id)
                # if city_obj is a parent (no parent), include its direct subcities
                try:
                    if getattr(city_obj, 'parent', None) is None:
                        for ch in city_obj.subcities.all():
                            if getattr(ch, 'id', None):
                                subcity_ids.add(ch.id)
                except Exception:
                    pass
                cand_ids |= subcity_ids

            exact_ids = {city_obj.id}
            if purpose == 'from':
                stops_iter = stops_list
            else:
                stops_iter = list(reversed(stops_list))

            for s in stops_iter:
                try:
                    if getattr(s, 'city', None) and getattr(s.city, 'id', None) in exact_ids:
                        return s
                except Exception:
                    continue

            if subcity_ids:
                for s in stops_iter:
                    try:
                        if getattr(s, 'city', None) and getattr(s.city, 'id', None) in subcity_ids:
                            return s
                    except Exception:
                        continue

        # If token appears to be a country, match city.country instead.
        country_code = normalize_country(token)
        if country_code:
            candidates = []
            for s in stops_list:
                try:
                    city_country = normalize_text(getattr(getattr(s, 'city', None), 'country', '') or '')
                    if not city_country:
                        continue
                    if normalize_country(city_country) == country_code:
                        candidates.append(s)
                except Exception:
                    continue
            if candidates:
                return candidates[0] if purpose == 'from' else candidates[-1]

        # fallback: match by stop name/address or fuzzy match
        for s in stops_list:
            city_name_raw = (s.city.name if getattr(s, 'city', None) else '') or ''
            city_name = normalize_text(city_name_raw)
            addr = normalize_text((s.address or ''))
            # direct substring matches (only when fields are non-empty)
            if city_name and (token_norm in city_name or city_name in token_norm):
                return s
            if addr and (token_norm in addr or addr in token_norm):
                return s
            # fuzzy match fallback
            if city_name and similar(token_norm, city_name) >= 0.60:
                return s
            if addr and similar(token_norm, addr) >= 0.60:
                return s
        return None

    trips_qs = Trip.objects.filter(active=True)
    if date_obj:
        unavail_qs = TripDayAvailability.objects.filter(trip=OuterRef('pk'), date=date_obj, available=False)
        avail_qs = TripDayAvailability.objects.filter(trip=OuterRef('pk'), date=date_obj, available=True)
        trips_qs = trips_qs.annotate(unavailable_on_date=Exists(unavail_qs), available_on_date=Exists(avail_qs))
        trips_qs = trips_qs.filter(unavailable_on_date=False).filter(
            Q(date__isnull=True) | Q(date=date_obj) | Q(available_on_date=True)
        )
    if direction:
        trips_qs = trips_qs.filter(direction=direction)

    # iterate trips and try flexible matching: match by TripStop city/address or RouteStop
    for trip in trips_qs.select_related('route'):
        trip_stops = list(trip.trip_stops.select_related('city').all())
        route_stops = list(trip.route.stops.select_related('city').all()) if trip.route_id else []

        from_stop = find_match(trip_stops, q_from, purpose='from') or find_match(route_stops, q_from, purpose='from')
        to_stop = find_match(trip_stops, q_to, purpose='to') or find_match(route_stops, q_to, purpose='to')

        # Fallback: if trip has explicit start/end cities, allow matching by them
        try:
            if (not from_stop) and getattr(trip, 'start_city', None):
                sc = trip.start_city.name.lower() if trip.start_city and trip.start_city.name else ''
                if sc and (q_from in sc or sc in q_from):
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
            pass

        if not from_stop or not to_stop:
            # no match for this trip
            continue

        from_order = getattr(from_stop, 'order', None)
        to_order = getattr(to_stop, 'order', None)
        if from_order is None or to_order is None:
            continue

        # ensure correct ordering depending on trip direction
        if trip.direction in ('UA_PL', 'UA_UA'):
            if from_order > to_order:
                continue
            if from_order == to_order:
                if not (getattr(trip, 'start_city', None) and getattr(trip, 'end_city', None)):
                    continue
                try:
                    if not (getattr(from_stop, 'city', None) and getattr(to_stop, 'city', None) and from_stop.city_id == trip.start_city_id and to_stop.city_id == trip.end_city_id):
                        continue
                except Exception:
                    continue
        elif trip.direction == 'PL_UA':
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
            stops_list.append({'time': time or '', 'place': place, 'price': float(getattr(s, 'price', 0.0) or 0.0)})

        # Compute price for segment: prefer explicit TripFare if present,
        # otherwise fall back to summing TripStop.price or Trip.base_price.
        price = 0.0
        currency = getattr(trip, 'currency', 'UAH') or 'UAH'
        fare_used = False
        fare_obj = None
        try:
            fare_obj = find_trip_fare(trip, getattr(from_stop, 'city', None), getattr(to_stop, 'city', None))

            # Fallback: if stop-based lookup didn't find a fare, try to resolve
            # cities from the user's raw `from`/`to` input (exact match, then
            # normalized match). This prevents wrong fuzzy stop matches (e.g.
            # matching 'Любомль' instead of 'Люблін') from hiding admin fares.
            if not fare_obj:
                try:
                    city_from = City.objects.filter(name__iexact=from_name).first()
                    city_to = City.objects.filter(name__iexact=to_name).first()
                    if not city_from or not city_to:
                        # build small normalized map
                        all_cities = list(City.objects.all())
                        norm_map = {normalize_text(c.name): c for c in all_cities}
                        if not city_from:
                            city_from = norm_map.get(normalize_text(from_name))
                        if not city_to:
                            city_to = norm_map.get(normalize_text(to_name))
                    if city_from and city_to:
                        try:
                            fare_obj = find_trip_fare(trip, city_from, city_to)
                        except Exception:
                            fare_obj = None
                except Exception:
                    pass

            if fare_obj and getattr(fare_obj, 'price', None) is not None and float(fare_obj.price or 0.0) > 0:
                price = float(fare_obj.price)
                if getattr(fare_obj, 'currency', None):
                    currency = fare_obj.currency
                fare_used = True
            else:
                # sum per-leg prices
                missing_price = False
                price_sum = 0.0
                if trip_stops:
                    for s in trip_stops:
                        so = getattr(s, 'order', None)
                        if so is None:
                            continue
                        if so >= from_order and so < to_order:
                            p = float(getattr(s, 'price', 0.0) or 0.0)
                            if p <= 0:
                                missing_price = True
                            price_sum += p
                if price_sum > 0 and not missing_price:
                    price = price_sum
                else:
                    price = float(trip.base_price or 0.0)
        except Exception:
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
            'currency': currency,
            'fare_used': bool(fare_used),
            'fare_id': (fare_obj.id if fare_obj else None),
            'discount_percent': discount,
            'price_after_discount': price_after,
            'free': trip.seats,
            'carrier': (trip.carrier_user.username if getattr(trip, 'carrier_user', None) else (trip.carrier or trip.title or (trip.route.name if trip.route else ''))),
            'stops': stops_list,
        })

    def departure_sort_key(item):
        depart = item.get('depart') or ''
        try:
            return datetime.strptime(depart, '%H:%M').time()
        except Exception:
            return datetime.max.time()

    results.sort(key=departure_sort_key)
    return JsonResponse({'trips': results})

def api_cities(request):
    """Return available cities grouped by country code (UA/PL).

    Optional query param: dir (UA_PL or PL_UA) — currently unused but accepted.
    """
    direction = request.GET.get('dir')

    # prefer explicit country flags; also include blank-country cities for autocomplete
    ua_ids = set(TripStop.objects.filter(trip__direction__in=['UA_PL', 'UA_UA']).values_list('city_id', flat=True))
    pl_ids = set(TripStop.objects.filter(trip__direction='PL_UA').values_list('city_id', flat=True))
    ua_qs = City.objects.filter(Q(country__iexact='UA') | Q(country='') | Q(country__isnull=True) | Q(id__in=ua_ids)).distinct().order_by('name')
    pl_qs = City.objects.filter(Q(country__iexact='PL') | Q(country='') | Q(country__isnull=True) | Q(id__in=pl_ids)).distinct().order_by('name')

    # if countries are not set and no cities were found this way, fall back to trip stops
    if not ua_qs.exists() or not pl_qs.exists():
        ua_qs = City.objects.filter(id__in=ua_ids).distinct().order_by('name') if ua_ids else ua_qs
        pl_qs = City.objects.filter(id__in=pl_ids).distinct().order_by('name') if pl_ids else pl_qs

    ua_list = [{'id': c.id, 'name': c.name} for c in ua_qs]
    pl_list = [{'id': c.id, 'name': c.name} for c in pl_qs]

    return JsonResponse({'UA': ua_list, 'PL': pl_list})


@login_required
def profile(request):
    profile = getattr(request.user, 'profile', None)
    if request.method == 'POST':
        form = ProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            form.save()
            try:
                messages.success(request, 'Профіль оновлено')
            except Exception:
                pass
            return redirect('main:profile')
    else:
        form = ProfileForm(instance=profile)

    # Exclude tickets for which an explicit per-day availability record marks the trip as unavailable
    try:
        tickets = (
            Ticket.objects.filter(user=request.user, paid=True)
            .annotate(
                is_canceled=Exists(
                    TripDayAvailability.objects.filter(
                        trip_id=OuterRef('trip_id'),
                        date=OuterRef('travel_date'),
                        available=False,
                    )
                )
            )
            .filter(is_canceled=False)
            .order_by('-created_at')
        )
    except Exception:
        tickets = Ticket.objects.filter(user=request.user, paid=True).order_by('-created_at')
    support_tickets = SupportTicket.objects.filter(user=request.user, is_archived=False).order_by('-last_message_at')

    # attach signature to each ticket for preview/download links
    try:
        for t in tickets:
            try:
                t.sig = _ticket_signature(t)
            except Exception:
                t.sig = ''
    except Exception:
        pass

    return render(request, 'profile.html', {'form': form, 'tickets': tickets, 'support_tickets': support_tickets})


@login_required
def dieller_reports(request):
    """Advanced statistics page available only for the `dieller` user."""
    if not request.user.is_authenticated or (getattr(request.user, 'username', '') or '').lower() != 'dieller':
        return HttpResponse(status=403)

    from django.db.models import Sum, Count, Q, F, Value, DecimalField, ExpressionWrapper
    from django.db.models.functions import Coalesce
    from datetime import timedelta, datetime

    today = timezone.localdate()
    filter_period = request.GET.get('filter', 'this_month')
    query = (request.GET.get('q') or '').strip()
    sort = request.GET.get('sort', 'sales_desc')
    carrier_id = request.GET.get('carrier_id')
    custom_start = request.GET.get('start_date')
    custom_end = request.GET.get('end_date')
    ajax_request = request.GET.get('ajax') == '1'

    def first_day_of_month(date_obj):
        return date_obj.replace(day=1)

    def next_month_first(date_obj):
        if date_obj.month == 12:
            return date_obj.replace(year=date_obj.year + 1, month=1, day=1)
        return date_obj.replace(month=date_obj.month + 1, day=1)

    if filter_period == 'today':
        start_date = today
        end_date = today + timedelta(days=1)
        period_label = 'Сьогодні'
    elif filter_period == 'yesterday':
        start_date = today - timedelta(days=1)
        end_date = today
        period_label = 'Вчора'
    elif filter_period == 'this_week':
        start_date = today - timedelta(days=today.weekday())
        end_date = start_date + timedelta(days=7)
        period_label = 'Цей тиждень'
    elif filter_period == 'last_month':
        first_of_this_month = first_day_of_month(today)
        last_month_end = first_of_this_month
        start_date = first_day_of_month(last_month_end - timedelta(days=1))
        end_date = last_month_end
        period_label = 'Минулий місяць'
    elif filter_period == 'custom':
        try:
            start_date = timezone.datetime.strptime(custom_start or '', '%Y-%m-%d').date()
        except Exception:
            start_date = first_day_of_month(today)
        try:
            end_date = timezone.datetime.strptime(custom_end or '', '%Y-%m-%d').date() + timedelta(days=1)
        except Exception:
            end_date = today + timedelta(days=1)
        period_label = f"{start_date.strftime('%d.%m.%Y')} — {(end_date - timedelta(days=1)).strftime('%d.%m.%Y')}"
    else:
        start_date = first_day_of_month(today)
        end_date = next_month_first(today)
        period_label = 'Цей місяць'

    if start_date >= end_date:
        start_date = first_day_of_month(today)
        end_date = next_month_first(today)
        period_label = 'Цей місяць'

    ticket_date_filter = Q(paid=True, created_at__date__gte=start_date, created_at__date__lt=end_date)
    tickets_qs = Ticket.objects.filter(ticket_date_filter)

    selected_carrier = None
    if carrier_id:
        try:
            selected_carrier = Carrier.objects.filter(pk=int(carrier_id)).first()
        except Exception:
            selected_carrier = None
    if selected_carrier and selected_carrier.user:
        tickets_qs = tickets_qs.filter(trip__carrier_user=selected_carrier.user)

    carrier_filter = Q()
    if query:
        carrier_filter = Q(company_name__icontains=query) | Q(email__icontains=query) | Q(phone__icontains=query)

    carrier_stats_qs = Carrier.objects.filter(carrier_filter).annotate(
        trips_count=Count(
            'user__carrier_trips',
            filter=Q(
                user__carrier_trips__tickets__paid=True,
                user__carrier_trips__tickets__created_at__date__gte=start_date,
                user__carrier_trips__tickets__created_at__date__lt=end_date,
            ),
            distinct=True,
        ),
        tickets_count=Count(
            'user__carrier_trips__tickets',
            filter=Q(
                user__carrier_trips__tickets__paid=True,
                user__carrier_trips__tickets__created_at__date__gte=start_date,
                user__carrier_trips__tickets__created_at__date__lt=end_date,
            ),
            distinct=True,
        ),
        total_sales=Coalesce(
            Sum(
                'user__carrier_trips__tickets__total_price',
                filter=Q(
                    user__carrier_trips__tickets__paid=True,
                    user__carrier_trips__tickets__created_at__date__gte=start_date,
                    user__carrier_trips__tickets__created_at__date__lt=end_date,
                )
            ),
            Value(0, output_field=DecimalField(max_digits=14, decimal_places=2)),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
        total_commission=Coalesce(
            Sum(
                ExpressionWrapper(
                    F('user__carrier_trips__tickets__total_price') * Value(Decimal('0.15')),
                    output_field=DecimalField(max_digits=14, decimal_places=2),
                ),
                filter=Q(
                    user__carrier_trips__tickets__paid=True,
                    user__carrier_trips__tickets__created_at__date__gte=start_date,
                    user__carrier_trips__tickets__created_at__date__lt=end_date,
                ),
            ),
            Value(0, output_field=DecimalField(max_digits=14, decimal_places=2)),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
    )

    sort_map = {
        'sales_desc': '-total_sales',
        'sales_asc': 'total_sales',
        'profit_desc': '-total_sales',
        'profit_asc': 'total_sales',
        'commission_desc': '-total_commission',
        'commission_asc': 'total_commission',
        'name_asc': 'company_name',
        'name_desc': '-company_name',
    }
    carrier_stats_qs = carrier_stats_qs.order_by(sort_map.get(sort, '-total_sales'))

    carrier_rows = []
    for c in carrier_stats_qs:
        total_sales = float(c.total_sales or 0)
        commission_amount = float(c.total_commission or 0)
        carrier_rows.append({
            'id': c.id,
            'name': c.company_name or c.username or (c.user.username if c.user else '—'),
            'email': c.email or (c.user.email if c.user else ''),
            'phone': c.phone,
            'trips_count': c.trips_count or 0,
            'tickets_count': c.tickets_count or 0,
            'total_sales': total_sales,
            'commission': round(commission_amount, 2),
            'status': 'active' if getattr(c.user, 'is_active', False) else 'inactive',
        })

    day_list = []
    day = start_date
    while day < end_date:
        day_list.append(day)
        day += timedelta(days=1)

    totals_by_day = {d: {'sales': 0.0, 'tickets': 0} for d in day_list}
    daily_qs = Ticket.objects.filter(ticket_date_filter)
    if selected_carrier and selected_carrier.user:
        daily_qs = daily_qs.filter(trip__carrier_user=selected_carrier.user)
    daily_data = daily_qs.values('created_at__date').annotate(total=Sum('total_price'), count=Count('id'))
    for item in daily_data:
        day_key = item.get('created_at__date')
        if day_key in totals_by_day:
            totals_by_day[day_key]['sales'] = float(item.get('total') or 0)
            totals_by_day[day_key]['tickets'] = item.get('count') or 0

    chart_labels = [d.strftime('%d.%m') for d in day_list]
    chart_sales = [totals_by_day[d]['sales'] for d in day_list]
    chart_tickets = [totals_by_day[d]['tickets'] for d in day_list]
    chart_profit = [round(v * 0.85, 2) for v in chart_sales]
    chart_labels_json = json.dumps(chart_labels)
    chart_sales_json = json.dumps(chart_sales)
    chart_tickets_json = json.dumps(chart_tickets)
    chart_profit_json = json.dumps(chart_profit)

    top_carriers = sorted(carrier_rows, key=lambda c: c['total_sales'], reverse=True)[:8]

    sales_summary_qs = Ticket.objects.filter(ticket_date_filter)
    if selected_carrier and selected_carrier.user:
        sales_summary_qs = sales_summary_qs.filter(trip__carrier_user=selected_carrier.user)

    sold_count = sales_summary_qs.count()
    sold_total = float(sales_summary_qs.aggregate(total=Coalesce(Sum('total_price'), Value(0, output_field=DecimalField(max_digits=14, decimal_places=2)), output_field=DecimalField(max_digits=14, decimal_places=2)))['total'] or 0)
    commission_total = float(sales_summary_qs.aggregate(
        total=Coalesce(
            Sum(ExpressionWrapper(F('total_price') * Value(Decimal('0.15')), output_field=DecimalField(max_digits=14, decimal_places=2))),
            Value(0, output_field=DecimalField(max_digits=14, decimal_places=2)),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
    )['total'] or 0)
    net_profit = round(sold_total - commission_total, 2)
    net_profit = round(sold_total - commission_total, 2)

    ticket_rows = []
    if selected_carrier and selected_carrier.user:
        ticket_qs = sales_summary_qs.select_related('user', 'trip__route').prefetch_related('passenger_set')

        def ticket_route(ticket):
            if ticket.trip and getattr(ticket.trip, 'route', None):
                return ticket.trip.route.name
            if ticket.from_city and ticket.to_city:
                return f"{ticket.from_city} → {ticket.to_city}"
            return ticket.route or '—'

        def ticket_time(ticket):
            if ticket.trip:
                first_stop = ticket.trip.trip_stops.order_by('order').first()
                if first_stop and getattr(first_stop, 'departure_time', None):
                    return first_stop.departure_time.strftime('%H:%M')
            return ''

        for ticket in ticket_qs.order_by('-created_at'):
            passengers = [f"{p.first_name} {p.last_name}".strip() for p in ticket.passenger_set.all()]
            passenger_label = ', '.join(passengers) if passengers else (ticket.user.username if ticket.user else '')
            seat_label = getattr(ticket, 'seat', None) or (f"{ticket.passengers} місць" if ticket.passengers else '—')
            ticket_rows.append({
                'id': ticket.id,
                'route': ticket_route(ticket),
                'date': ticket.travel_date.strftime('%d.%m.%Y') if ticket.travel_date else '',
                'time': ticket_time(ticket),
                'seats': seat_label,
                'passenger': passenger_label,
                'phone': ticket.contact_phone or '',
                'email': ticket.contact_email or '',
                'price': float(ticket.total_price or 0),
                'status': 'Оплачено' if ticket.paid else 'Не оплачено',
                'created_at': ticket.created_at.strftime('%d.%m.%Y %H:%M') if ticket.created_at else '',
            })

    if ajax_request:
        return JsonResponse({
            'summary': {
                'tickets': sold_count,
                'sales': sold_total,
                'commission': commission_total,
            },
            'chart_labels': chart_labels,
            'chart_sales': chart_sales,
            'chart_profit': chart_profit,
            'chart_tickets': chart_tickets,
            'top_carriers': top_carriers,
            'carrier_selected_id': selected_carrier.id if selected_carrier else None,
            'tickets': ticket_rows,
        })

    return render(request, 'dieller_reports.html', {
        'period_label': period_label,
        'filter_period': filter_period,
        'query': query,
        'sort': sort,
        'carriers': carrier_rows,
        'selected_carrier': selected_carrier,
        'carrier_tickets': ticket_rows,
        'carrier_selected_id': selected_carrier.id if selected_carrier else None,
        'summary': {
            'tickets': sold_count,
            'sales': sold_total,
            'commission': commission_total,
            'profit': net_profit,
        },
        'chart_labels_json': chart_labels_json,
        'chart_sales_json': chart_sales_json,
        'chart_tickets_json': chart_tickets_json,
        'top_carriers': top_carriers,
        'start_date': start_date.strftime('%Y-%m-%d'),
        'end_date': (end_date - timedelta(days=1)).strftime('%Y-%m-%d'),
        'filters': {
            'today': filter_period == 'today',
            'yesterday': filter_period == 'yesterday',
            'this_week': filter_period == 'this_week',
            'this_month': filter_period == 'this_month',
            'last_month': filter_period == 'last_month',
            'custom': filter_period == 'custom',
        },
    })


def _route_report_context(request):
    from datetime import datetime, timedelta
    from django.db.models import Q

    today = timezone.localdate()
    filter_period = request.GET.get('filter', 'this_month')
    custom_start = request.GET.get('start_date')
    custom_end = request.GET.get('end_date')
    route_id = request.GET.get('route_id')

    def first_day_of_month(date_obj):
        return date_obj.replace(day=1)

    def last_day_of_month(date_obj):
        next_month = date_obj.replace(day=28) + timedelta(days=4)
        return next_month.replace(day=1) - timedelta(days=1)

    if filter_period == 'today':
        start_date = today
        end_date = today
        period_label = 'Сьогодні'
    elif filter_period == 'yesterday':
        start_date = today - timedelta(days=1)
        end_date = start_date
        period_label = 'Вчора'
    elif filter_period == 'this_week':
        start_date = today - timedelta(days=today.weekday())
        end_date = start_date + timedelta(days=6)
        period_label = 'Цей тиждень'
    elif filter_period == 'last_month':
        first_of_this_month = first_day_of_month(today)
        last_month_end = first_of_this_month - timedelta(days=1)
        start_date = first_day_of_month(last_month_end)
        end_date = last_month_end
        period_label = 'Минулий місяць'
    elif filter_period == 'custom':
        try:
            start_date = datetime.strptime(custom_start or '', '%Y-%m-%d').date()
        except Exception:
            start_date = first_day_of_month(today)
        try:
            end_date = datetime.strptime(custom_end or '', '%Y-%m-%d').date()
        except Exception:
            end_date = today
        period_label = f"{start_date.strftime('%d.%m.%Y')} — {end_date.strftime('%d.%m.%Y')}"
    else:
        start_date = first_day_of_month(today)
        end_date = last_day_of_month(today)
        period_label = 'Цей місяць'

    if start_date > end_date:
        start_date = first_day_of_month(today)
        end_date = last_day_of_month(today)
        period_label = 'Цей місяць'

    routes = list(Route.objects.filter(active=True).order_by('name'))
    selected_route = None
    if route_id:
        try:
            selected_route = Route.objects.filter(pk=int(route_id)).first()
        except Exception:
            selected_route = None

    date_filter = Q(paid=True) & (
        Q(travel_date__gte=start_date, travel_date__lte=end_date)
        | Q(trip__date__gte=start_date, trip__date__lte=end_date)
    )

    tickets_qs = Ticket.objects.filter(date_filter).select_related('trip__route', 'trip__carrier_user').prefetch_related('trip__trip_stops').order_by('-travel_date', '-created_at')
    if selected_route:
        tickets_qs = tickets_qs.filter(trip__route=selected_route)

    def ticket_route_label(ticket):
        if getattr(ticket, 'trip', None) and getattr(getattr(ticket, 'trip', None), 'route', None):
            return ticket.trip.route.name
        if ticket.from_city and ticket.to_city:
            return f"{ticket.from_city} → {ticket.to_city}"
        return ticket.route or '—'

    def ticket_date_value(ticket):
        if getattr(ticket, 'travel_date', None):
            return ticket.travel_date
        if getattr(ticket, 'trip', None) and getattr(ticket.trip, 'date', None):
            return ticket.trip.date
        return None

    def ticket_depart_time(ticket):
        if getattr(ticket, 'trip', None):
            try:
                stop = ticket.trip.trip_stops.order_by('order').first()
                if stop and getattr(stop, 'departure_time', None):
                    return stop.departure_time.strftime('%H:%M')
            except Exception:
                pass
        return ''

    total_tickets = 0
    total_sales = 0.0
    total_commission = 0.0
    total_carrier_pay = 0.0
    currency_totals = {}
    route_totals = {}
    trip_totals = {}
    daily_totals = {}
    ticket_rows = []

    for ticket in tickets_qs:
        route_label = ticket_route_label(ticket)
        travel_date = ticket_date_value(ticket)
        if not travel_date:
            continue

        price = float(ticket.total_price or 0.0)
        commission = round(price * 0.15, 2)
        carrier_pay = round(price - commission, 2)
        currency = ticket.currency or 'UAH'
        trip_label = 'Без рейсу'
        trip_id = None
        if getattr(ticket, 'trip', None):
            trip_id = ticket.trip.id
            trip_label = ticket.trip.title or f"Рейс #{ticket.trip.id}"

        total_tickets += 1
        total_sales += price
        total_commission += commission
        total_carrier_pay += carrier_pay

        currency_totals.setdefault(currency, {'tickets': 0, 'sales': 0.0, 'commission': 0.0, 'carrier_pay': 0.0})
        currency_totals[currency]['tickets'] += 1
        currency_totals[currency]['sales'] += price
        currency_totals[currency]['commission'] += commission
        currency_totals[currency]['carrier_pay'] += carrier_pay

        route_totals.setdefault(route_label, {'tickets': 0, 'sales': 0.0, 'commission': 0.0, 'carrier_pay': 0.0})
        route_totals[route_label]['tickets'] += 1
        route_totals[route_label]['sales'] += price
        route_totals[route_label]['commission'] += commission
        route_totals[route_label]['carrier_pay'] += carrier_pay

        if trip_id is not None:
            trip_totals.setdefault(trip_id, {'trip': ticket.trip, 'tickets': 0, 'sales': 0.0, 'commission': 0.0, 'carrier_pay': 0.0})
            trip_totals[trip_id]['tickets'] += 1
            trip_totals[trip_id]['sales'] += price
            trip_totals[trip_id]['commission'] += commission
            trip_totals[trip_id]['carrier_pay'] += carrier_pay

        daily_totals.setdefault(travel_date, {'sales': 0.0, 'tickets': 0})
        daily_totals[travel_date]['sales'] += price
        daily_totals[travel_date]['tickets'] += 1

        ticket_rows.append({
            'id': ticket.id,
            'route': route_label,
            'trip': trip_label,
            'date': travel_date.strftime('%d.%m.%Y'),
            'time': ticket_depart_time(ticket),
            'passengers': ticket.passengers,
            'phone': ticket.contact_phone or '',
            'email': ticket.contact_email or '',
            'price': price,
            'currency': currency,
            'commission': commission,
            'carrier_pay': carrier_pay,
        })

    day_list = []
    current_day = start_date
    while current_day <= end_date:
        day_list.append(current_day)
        current_day += timedelta(days=1)

    chart_labels = [d.strftime('%d.%m') for d in day_list]
    chart_sales = [round(daily_totals.get(d, {}).get('sales', 0.0), 2) for d in day_list]
    chart_tickets = [daily_totals.get(d, {}).get('tickets', 0) for d in day_list]

    route_rows = [
        {
            'route': name,
            'tickets': values['tickets'],
            'sales': round(values['sales'], 2),
            'commission': round(values['commission'], 2),
            'carrier_pay': round(values['carrier_pay'], 2),
        }
        for name, values in route_totals.items()
    ]
    route_rows.sort(key=lambda x: x['sales'], reverse=True)

    trip_rows = [
        {
            'trip_id': trip_id,
            'trip': values['trip'],
            'tickets': values['tickets'],
            'sales': round(values['sales'], 2),
            'commission': round(values['commission'], 2),
            'carrier_pay': round(values['carrier_pay'], 2),
        }
        for trip_id, values in trip_totals.items()
    ]
    trip_rows.sort(key=lambda x: x['sales'], reverse=True)

    currency_rows = [
        {
            'currency': currency,
            'tickets': values['tickets'],
            'sales': round(values['sales'], 2),
            'commission': round(values['commission'], 2),
            'carrier_pay': round(values['carrier_pay'], 2),
        }
        for currency, values in currency_totals.items()
    ]
    currency_rows.sort(key=lambda x: x['sales'], reverse=True)

    return {
        'filter_period': filter_period,
        'period_label': period_label,
        'routes': routes,
        'selected_route': selected_route,
        'selected_route_id': selected_route.id if selected_route else None,
        'ticket_rows': ticket_rows,
        'route_rows': route_rows,
        'trip_rows': trip_rows,
        'currency_rows': currency_rows,
        'summary': {
            'tickets': total_tickets,
            'sales': round(total_sales, 2),
            'commission': round(total_commission, 2),
            'carrier_pay': round(total_carrier_pay, 2),
        },
        'chart_labels_json': json.dumps(chart_labels),
        'chart_sales_json': json.dumps(chart_sales),
        'chart_tickets_json': json.dumps(chart_tickets),
        'start_date': start_date.strftime('%Y-%m-%d'),
        'end_date': end_date.strftime('%Y-%m-%d'),
        'filters': {
            'today': filter_period == 'today',
            'yesterday': filter_period == 'yesterday',
            'this_week': filter_period == 'this_week',
            'this_month': filter_period == 'this_month',
            'last_month': filter_period == 'last_month',
            'custom': filter_period == 'custom',
        },
    }


@login_required
def route_reports(request):
    if not request.user.is_authenticated or (getattr(request.user, 'username', '') or '').lower() != 'dieller':
        return HttpResponse(status=403)

    context = _route_report_context(request)
    return render(request, 'route_reports.html', context)


@login_required
def route_reports_export_excel(request):
    if not request.user.is_authenticated or (getattr(request.user, 'username', '') or '').lower() != 'dieller':
        return HttpResponse(status=403)

    context = _route_report_context(request)
    out = io.StringIO()
    out.write('\ufeff')
    writer = csv.writer(out)
    writer.writerow(['ticket_id', 'route', 'trip', 'travel_date', 'departure_time', 'passengers', 'phone', 'email', 'price', 'currency', 'commission', 'carrier_pay'])
    for row in context['ticket_rows']:
        writer.writerow([
            row['id'],
            row['route'],
            row['trip'],
            row['date'],
            row['time'],
            row['passengers'],
            row['phone'],
            row['email'],
            f"{row['price']:.2f}",
            row['currency'],
            f"{row['commission']:.2f}",
            f"{row['carrier_pay']:.2f}",
        ])

    filename = f"route_report_{context['start_date']}_{context['end_date']}.csv"
    resp = HttpResponse(out.getvalue(), content_type='text/csv; charset=utf-8')
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp


@login_required
def route_reports_export_pdf(request):
    if not request.user.is_authenticated or (getattr(request.user, 'username', '') or '').lower() != 'dieller':
        return HttpResponse(status=403)

    if A4 is None:
        return HttpResponse('PDF generation is not available in this environment.', status=500)

    context = _route_report_context(request)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    margin = 40
    y = height - margin

    c.setFont('Helvetica-Bold', 14)
    c.drawString(margin, y, 'Звіт за маршрутами')
    c.setFont('Helvetica', 10)
    y -= 20
    c.drawString(margin, y, f"Період: {context['period_label']}")
    if context['selected_route']:
        y -= 14
        c.drawString(margin, y, f"Маршрут: {context['selected_route'].name}")

    y -= 24
    c.drawString(margin, y, f"Квитків: {context['summary']['tickets']}")
    c.drawString(margin + 140, y, f"Дохід: {context['summary']['sales']:.2f}")
    c.drawString(margin + 300, y, f"Комісія: {context['summary']['commission']:.2f}")
    c.drawString(margin + 430, y, f"Перевізнику: {context['summary']['carrier_pay']:.2f}")

    y -= 24
    c.setFont('Helvetica-Bold', 10)
    headers = ['ID', 'Дата', 'Маршрут', 'Ціна', 'Валюта', 'Комісія', 'Перевізнику']
    x_positions = [margin, margin+40, margin+90, margin+310, margin+360, margin+420, margin+480]
    for x, label in zip(x_positions, headers):
        c.drawString(x, y, label)

    c.setFont('Helvetica', 9)
    y -= 16

    for row in context['ticket_rows']:
        if y < margin + 40:
            c.showPage()
            y = height - margin
            c.setFont('Helvetica-Bold', 10)
            for x, label in zip(x_positions, headers):
                c.drawString(x, y, label)
            c.setFont('Helvetica', 9)
            y -= 16

        c.drawString(x_positions[0], y, str(row['id']))
        c.drawString(x_positions[1], y, row['date'])
        c.drawString(x_positions[2], y, row['route'][:32])
        c.drawRightString(x_positions[3] + 40, y, f"{row['price']:.2f}")
        c.drawString(x_positions[4], y, row['currency'])
        c.drawRightString(x_positions[5] + 40, y, f"{row['commission']:.2f}")
        c.drawRightString(x_positions[6] + 40, y, f"{row['carrier_pay']:.2f}")
        y -= 14

    c.save()
    buf.seek(0)
    resp = HttpResponse(buf.getvalue(), content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="route_report_{context['start_date']}_{context['end_date']}.pdf"'
    return resp


@login_required
def carrier_dashboard(request):
    profile = getattr(request.user, 'profile', None)
    if not profile or not getattr(profile, 'is_carrier', False):
        return HttpResponse(status=403)

    from django.db.models.functions import ExtractMonth
    from django.db.models import Count, Sum
    from datetime import date

    today = date.today()
    try:
        year = int(request.GET.get('year') or today.year)
    except Exception:
        year = today.year

    if year == today.year:
        months_range = list(range(1, today.month + 1))
    else:
        months_range = list(range(1, 13))

    # optional trip filter (select a specific trip to inspect)
    trip_id = request.GET.get('trip_id')
    qs = Ticket.objects.filter(trip__carrier_user=request.user, paid=True, created_at__year=year)
    selected_trip = None
    selected_trip_id = None
    try:
        if trip_id:
            selected_trip_id = int(trip_id)
            qs = qs.filter(trip_id=selected_trip_id)
            selected_trip = Trip.objects.filter(pk=selected_trip_id, carrier_user=request.user).first()
    except Exception:
        selected_trip = None
    months_qs = qs.annotate(month=ExtractMonth('created_at')).values('month').annotate(count=Count('id'), total=Sum('total_price')).order_by('month')
    stats_map = {m['month']: {'count': m.get('count') or 0, 'total': m.get('total') or 0} for m in months_qs}

    MONTH_NAMES_UA = {1: 'Січень', 2: 'Лютий', 3: 'Березень', 4: 'Квітень', 5: 'Травень', 6: 'Червень', 7: 'Липень', 8: 'Серпень', 9: 'Вересень', 10: 'Жовтень', 11: 'Листопад', 12: 'Грудень'}

    months = []
    for mo in months_range:
        st = stats_map.get(mo, {'count': 0, 'total': 0})
        months.append({
            'year': year,
            'month': mo,
            'name': MONTH_NAMES_UA.get(mo, str(mo)),
            'count': st['count'] or 0,
            'total': float(st['total'] or 0),
        })

    # also provide list of this carrier's trips for quick selection
    trips = Trip.objects.filter(carrier_user=request.user).order_by('date')

    return render(request, 'carrier/dashboard.html', {
        'months': months,
        'year': year,
        'trips': trips,
        'selected_trip': selected_trip,
        'selected_trip_id': selected_trip_id,
    })


@login_required
def carrier_tickets_month(request, year, month):
    profile = getattr(request.user, 'profile', None)
    if not profile or not getattr(profile, 'is_carrier', False):
        return HttpResponse(status=403)

    try:
        year = int(year)
        month = int(month)
    except Exception:
        return HttpResponse(status=400)
    # Prefetch related payments (most recent first) and passengers to minimize per-row queries
    from collections import OrderedDict
    from django.db.models import Prefetch
    from .models import Payment, Passenger

    tickets = Ticket.objects.filter(
        trip__carrier_user=request.user,
        paid=True,
        created_at__year=year,
        created_at__month=month,
    ).select_related('user', 'trip').prefetch_related(
        Prefetch('payments', queryset=Payment.objects.order_by('-created_at'), to_attr='payments_prefetched'),
        Prefetch('passenger_set', queryset=Passenger.objects.order_by('id'), to_attr='passengers_list'),
    ).order_by('-created_at')

    # allow filtering to a single trip via GET param
    sel_trip_id = request.GET.get('trip_id')
    selected_trip = None
    try:
        if sel_trip_id:
            sel_trip_id = int(sel_trip_id)
            tickets = tickets.filter(trip_id=sel_trip_id)
            selected_trip = Trip.objects.filter(pk=sel_trip_id, carrier_user=request.user).first()
    except Exception:
        selected_trip = None

    # group tickets by purchase day and attach convenient attributes used by template
    days = OrderedDict()
    total = 0.0
    total_count = 0
    for t in tickets:
        # attach last payment and passenger names for template
        try:
            last = (t.payments_prefetched[0] if getattr(t, 'payments_prefetched', None) else None)
        except Exception:
            last = None
        t.last_payment = last
        try:
            pnames = []
            for p in getattr(t, 'passengers_list', []):
                name = f"{(p.first_name or '').strip()} {(p.last_name or '').strip()}".strip()
                if name:
                    pnames.append(name)
            t.passengers_display = ', '.join(pnames)
        except Exception:
            t.passengers_display = ''

        day = t.created_at.date() if getattr(t, 'created_at', None) else None
        if day not in days:
            days[day] = {'tickets': [], 'total': 0.0}
        days[day]['tickets'].append(t)
        try:
            pval = float(t.total_price or 0)
            days[day]['total'] += pval
            total += pval
        except Exception:
            pass
        total_count += 1

    # human-readable month name
    MONTH_NAMES_UA = {1: 'Січень', 2: 'Лютий', 3: 'Березень', 4: 'Квітень', 5: 'Травень', 6: 'Червень', 7: 'Липень', 8: 'Серпень', 9: 'Вересень', 10: 'Жовтень', 11: 'Листопад', 12: 'Грудень'}
    month_name = MONTH_NAMES_UA.get(month, str(month))

    return render(request, 'carrier/tickets_month.html', {
        'days': days,
        'year': year,
        'month': month,
        'total': total,
        'total_count': total_count,
        'month_name': month_name,
        'selected_trip': selected_trip,
    })


@login_required
def carrier_tickets_month_export_csv(request, year, month):
    profile = getattr(request.user, 'profile', None)
    if not profile or not getattr(profile, 'is_carrier', False):
        return HttpResponse(status=403)
    try:
        year = int(year)
        month = int(month)
    except Exception:
        return HttpResponse(status=400)

    tickets = Ticket.objects.filter(trip__carrier_user=request.user, paid=True, created_at__year=year, created_at__month=month).select_related('user', 'trip').order_by('-created_at')

    import io as _io
    out = _io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['ticket_id', 'user', 'email', 'trip_id', 'from_city', 'to_city', 'travel_date', 'passengers', 'total_price', 'currency', 'contact_phone', 'created_at'])
    total = 0
    for t in tickets:
        writer.writerow([
            t.id,
            getattr(t.user, 'username', ''),
            getattr(t.user, 'email', ''),
            (t.trip.id if getattr(t, 'trip', None) else ''),
            t.from_city or '',
            t.to_city or '',
            (t.travel_date.isoformat() if getattr(t, 'travel_date', None) else ''),
            t.passengers,
            str(t.total_price),
            t.currency or '',
            t.contact_phone or '',
            (t.created_at.isoformat() if getattr(t, 'created_at', None) else ''),
        ])
        try:
            total += float(t.total_price or 0)
        except Exception:
            pass

    # append summary row
    writer.writerow([])
    writer.writerow(['TOTAL', '', '', '', '', '', '', '', str(total), ''])

    resp = HttpResponse(out.getvalue(), content_type='text/csv; charset=utf-8')
    resp['Content-Disposition'] = f'attachment; filename="tickets_{year}_{month}.csv"'
    return resp


@login_required
def carrier_tickets_month_export_pdfs(request, year, month):
    profile = getattr(request.user, 'profile', None)
    if not profile or not getattr(profile, 'is_carrier', False):
        return HttpResponse(status=403)
    try:
        year = int(year)
        month = int(month)
    except Exception:
        return HttpResponse(status=400)

    tickets = Ticket.objects.filter(trip__carrier_user=request.user, paid=True, created_at__year=year, created_at__month=month).select_related('user', 'trip').order_by('-created_at')

    buf = io.BytesIO()
    z = zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED)
    added = 0
    for t in tickets:
        try:
            pdf_bytes = _generate_ticket_pdf_bytes(t, request)
            if pdf_bytes and isinstance(pdf_bytes, (bytes, bytearray)) and pdf_bytes.strip().startswith(b'%PDF'):
                z.writestr(f'ticket_{t.id}.pdf', pdf_bytes)
                added += 1
        except Exception:
            continue
    z.close()
    buf.seek(0)
    if added == 0:
        return HttpResponse('Немає PDF для експорту', status=404)
    resp = HttpResponse(buf.getvalue(), content_type='application/zip')
    resp['Content-Disposition'] = f'attachment; filename="tickets_{year}_{month}.zip"'
    return resp


@login_required
def carrier_manage_trips(request):
    profile = getattr(request.user, 'profile', None)
    if not profile or not getattr(profile, 'is_carrier', False):
        return HttpResponse(status=403)

    trips = Trip.objects.filter(carrier_user=request.user).order_by('date')
    return render(request, 'carrier/manage_trips.html', {'trips': trips})


@login_required
def carrier_toggle_trip(request, trip_id):
    profile = getattr(request.user, 'profile', None)
    if not profile or not getattr(profile, 'is_carrier', False):
        return HttpResponse(status=403)
    trip = get_object_or_404(Trip, pk=trip_id)
    # allow only assigned carrier or staff
    if not (request.user.is_staff or (trip.carrier_user_id and trip.carrier_user_id == request.user.id)):
        return HttpResponse(status=403)
    if request.method != 'POST':
        return HttpResponse(status=405)
    trip.active = not bool(trip.active)
    trip.save(update_fields=['active'])
    messages.info(request, f'Рейс "{trip.title or trip.id}" тепер: {"Їде" if trip.active else "Не їде"}')
    return redirect('main:carrier_manage_trips')


@login_required
def carrier_trip_availability(request, trip_id):
    profile = getattr(request.user, 'profile', None)
    if not profile or not getattr(profile, 'is_carrier', False):
        return HttpResponse(status=403)
    trip = get_object_or_404(Trip, pk=trip_id)
    if not (request.user.is_staff or (trip.carrier_user_id and trip.carrier_user_id == request.user.id)):
        return HttpResponse(status=403)

    from datetime import date, timedelta

    # default window of days to manage
    days_window = int(request.GET.get('days', 14))
    today = date.today()
    dates = [today + timedelta(days=i) for i in range(days_window)]

    if request.method == 'POST':
        # posted available dates as ISO strings
        posted = request.POST.getlist('available_dates')
        # normalize to date objects
        posted_set = set()
        for s in posted:
            try:
                posted_set.add(date.fromisoformat(s))
            except Exception:
                continue

        # ensure TripDayAvailability exists/updated for each date in window
        for d in dates:
            rec = trip.day_availabilities.filter(date=d).first()
            should_available = (d in posted_set)
            if rec:
                if rec.available != should_available:
                    rec.available = should_available
                    rec.save(update_fields=['available'])
            else:
                TripDayAvailability.objects.create(trip=trip, date=d, available=should_available)

        messages.success(request, 'Доступність рейсу оновлено')
        return redirect('main:carrier_trip_availability', trip_id=trip.id)

    # build list of dates with current availability
    days = []
    for d in dates:
        rec = trip.day_availabilities.filter(date=d).first()
        if rec is None:
            available = True
        else:
            available = bool(rec.available)
        days.append({'date': d, 'available': available})

    return render(request, 'carrier/trip_availability.html', {'trip': trip, 'days': days})


def redirect_to_home(request):
    return HttpResponseRedirect('/home/')


def logout_view(request):
    """Log out the current user (accept GET and POST) and redirect home."""
    try:
        auth_logout(request)
    except Exception:
        pass
    return redirect('main:home')


# ========================
# РЕЄСТРАЦІЯ + ЛОГІН
# ========================

def registerindex(request):
    # support 'next' parameter so after login/registration user can be returned to original page
    next_url = request.GET.get('next') or request.POST.get('next') or ''
    if next_url and not next_url.startswith('/'):
        # only allow internal next paths for safety
        next_url = ''

    context = {'next': next_url}

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

            # Create an inactive user and store verification flow
            try:
                with transaction.atomic():
                    user = User.objects.create_user(
                        username=username,
                        email=email,
                        password=password,
                        is_active=False
                    )

                    # 6-digit verification code
                    code = str(random.randint(100000, 999999))

                    EmailVerification.objects.create(
                        user=user,
                        code=code
                    )

                # Send verification email (best-effort)
                try:
                    html = render_to_string('emails/verification_email.html', {'code': code})
                    msg = EmailMultiAlternatives(
                        "Підтвердження email — Dieller Bus",
                        f"Ваш код підтвердження: {code}",
                        settings.DEFAULT_FROM_EMAIL,
                        [email],
                    )
                    msg.attach_alternative(html, "text/html")
                    # Use a short SMTP timeout and an explicit connection so a blocked
                    # SMTP server doesn't hang or kill gunicorn workers. Configure
                    # timeout via the EMAIL_TIMEOUT env var (seconds).
                    from django.core.mail import get_connection
                    timeout = int(os.environ.get('EMAIL_TIMEOUT', '10'))
                    conn = get_connection(timeout=timeout)
                    try:
                        conn.send_messages([msg])
                    finally:
                        try:
                            conn.close()
                        except Exception:
                            pass
                except Exception:
                    try:
                        send_mail(
                            "Підтвердження email — Dieller Bus",
                            f"Ваш код підтвердження: {code}",
                            getattr(settings, 'DEFAULT_FROM_EMAIL', None),
                            [email],
                            fail_silently=True,
                        )
                    except Exception:
                        pass

                request.session["verify_user_id"] = user.id
                if next_url:
                    request.session['verify_next'] = next_url
                return redirect("main:verify_email")
            except IntegrityError:
                context["register_error"] = "Такий логін або пошта вже існують"
                return render(request, "registerindex.html", context)

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
            if next_url:
                return redirect(next_url)
            return redirect("main:home")

    return render(request, "registerindex.html", context)


# ========================
# ПІДТВЕРДЖЕННЯ EMAIL
# ========================

def verify_email(request):
    user_id = request.session.get("verify_user_id")

    if not user_id:
        return redirect("main:registerindex")

    user = User.objects.get(id=user_id)
    verification = EmailVerification.objects.get(user=user)

    if request.method == "POST":
        code = request.POST.get("code")

        if verification.code == code:
            user.is_active = True
            user.save()
            verification.delete()

            auth_login(request, user)
            # If a next URL was saved before verification, redirect there
            next_after = request.session.pop('verify_next', None)
            if next_after and isinstance(next_after, str) and next_after.startswith('/'):
                return redirect(next_after)
            return redirect("main:profile")

        return render(request, "verify_email.html", {
            "error": "Невірний код",
            "email": user.email,
        })

    return render(request, "verify_email.html", {"email": user.email})


@require_POST
def resend_verification_code(request):
    user_id = request.session.get("verify_user_id")
    if not user_id:
        return JsonResponse({"ok": False, "message": "Сесія не знайдена"}, status=400)

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({"ok": False, "message": "Користувача не знайдено"}, status=400)

    try:
        code = str(random.randint(100000, 999999))
        EmailVerification.objects.update_or_create(user=user, defaults={"code": code})

        try:
            html = render_to_string('emails/verification_email.html', {'code': code})
            msg = EmailMultiAlternatives(
                "Підтвердження email — Dieller Bus",
                f"Ваш код підтвердження: {code}",
                settings.DEFAULT_FROM_EMAIL,
                [user.email],
            )
            msg.attach_alternative(html, "text/html")
            msg.send(fail_silently=False)
        except Exception:
            try:
                send_mail(
                    "Підтвердження email — Dieller Bus",
                    f"Ваш код підтвердження: {code}",
                    getattr(settings, 'DEFAULT_FROM_EMAIL', None),
                    [user.email],
                    fail_silently=True,
                )
            except Exception:
                pass

        return JsonResponse({"ok": True, "message": f"Код надіслано на {user.email}"})
    except Exception:
        return JsonResponse({"ok": False, "message": "Не вдалося надіслати код"}, status=500)


def check_user_availability(request):
    """API endpoint to check username/email availability (used by registration JS)."""
    username = request.GET.get('username') or request.POST.get('username')
    email = request.GET.get('email') or request.POST.get('email')
    resp = {}
    try:
        if username is not None:
            resp['username_available'] = not User.objects.filter(username__iexact=username).exists()
        if email is not None:
            resp['email_available'] = not User.objects.filter(email__iexact=email).exists()
    except Exception:
        resp['error'] = 'error'
    return JsonResponse(resp)


@csrf_exempt
def payment_success(request):
    """Handle post-payment redirect from WayForPay.

    Detects signed WayForPay payload, updates Payment/Ticket state, sends ticket email,
    and renders a friendly success page.
    """
    ticket = None
    payment = None
    processed = False

    order_reference = (
        request.POST.get('orderReference')
        or request.GET.get('orderReference')
        or request.POST.get('order_id')
        or request.GET.get('order_id')
        or request.POST.get('order')
        or request.GET.get('order')
        or request.POST.get('ticket')
        or request.GET.get('ticket')
        or ''
    ).strip()
    merchant_signature = request.POST.get('merchantSignature') or request.GET.get('merchantSignature')
    payload_query = request.POST if request.method == 'POST' else request.GET
    payload = dict(payload_query.lists())
    if not processed and order_reference and merchant_signature:
        try:
            if verify_wayforpay_signature(merchant_signature, payload_query):
                if order_reference.startswith('ticket-'):
                    try:
                        ticket_id = int(order_reference.split('-', 1)[1])
                        ticket = Ticket.objects.filter(pk=ticket_id).first()
                    except Exception:
                        ticket = None
                status_value = (
                    request.POST.get('reasonCode')
                    or request.GET.get('reasonCode')
                    or request.POST.get('transactionStatus')
                    or request.GET.get('transactionStatus')
                    or request.POST.get('status')
                    or request.GET.get('status')
                    or ''
                ).strip()
                success = str(status_value).lower() in ('1100', 'approved', 'accept', 'success', 'successfullypaid')
                if ticket and not success:
                    _delete_failed_ticket(ticket)
                    try:
                        request.session.pop('last_ticket_id', None)
                    except Exception:
                        pass
                    return redirect('main:payment_cancel')
                if ticket:
                    payment = _apply_wayforpay_result(
                        ticket,
                        None,
                        request,
                        payload,
                        success,
                        transaction_id=(request.POST.get('invoiceId') or request.GET.get('invoiceId') or request.POST.get('transactionId') or request.GET.get('transactionId') or ''),
                    )
                    processed = payment is not None
        except Exception:
            pass

    # 2) If no provider payload, allow query param orderReference/order_id=ticket-<id>
    if not processed and not ticket and order_reference and order_reference.startswith('ticket-'):
        try:
            ticket_id = int(order_reference.split('-', 1)[1])
            ticket = Ticket.objects.filter(pk=ticket_id).first()
        except Exception:
            ticket = None

    # 3) Fallback for environments where server-to-server callback isn't available
    # If we have an order in session or a success GET status in DEBUG, mark ticket paid.
    if not processed and not ticket:
        # try session-based fallback (user returned after creating ticket)
        try:
            last_tid = request.session.get('last_ticket_id')
            if last_tid:
                ticket = Ticket.objects.filter(pk=last_tid).first()
        except Exception:
            ticket = None

    if not processed and ticket:
        status_param = (
            request.POST.get('status')
            or request.GET.get('status')
            or request.POST.get('reasonCode')
            or request.GET.get('reasonCode')
            or request.POST.get('transactionStatus')
            or request.GET.get('transactionStatus')
            or ''
        ).lower()
        allow_fallback = settings.DEBUG or (status_param in ('success', 'processing', 'accept', 'approved'))
        if allow_fallback:
            try:
                provider_tx = request.GET.get('transaction_id') or request.GET.get('payment_id') or request.GET.get('provider_id') or ''
                payload = {'fallback_marked': True, 'query': dict(request.GET)}
                payment = _apply_wayforpay_result(ticket, None, request, payload, True, transaction_id=provider_tx)
                try:
                    request.session.pop('last_ticket_id', None)
                except Exception:
                    pass
                processed = payment is not None
            except Exception:
                pass

    # If this payment was part of an exchange flow, apply credit for the old ticket
    try:
        exch_old_id = None
        try:
            exch_old_id = request.session.pop('exchange_old_ticket_id', None)
        except Exception:
            exch_old_id = None
        if exch_old_id and ticket and ticket.paid:
            try:
                from .models import Payment
                old_ticket = Ticket.objects.filter(pk=int(exch_old_id)).first()
                if old_ticket:
                    refund_amount = float(old_ticket.total_price or 0.0)
                    # ledger: create negative payment record (manual refund via balance)
                    try:
                        Payment.objects.create(ticket=None, user=old_ticket.user, amount=-abs(refund_amount), currency=(old_ticket.currency or 'UAH'), status='refunded', provider='exchange', data={'by': request.user.username if request.user.is_authenticated else 'system', 'old_ticket': old_ticket.id, 'new_ticket': ticket.id})
                    except Exception:
                        pass
                    try:
                        profile = old_ticket.user.profile
                        profile.balance = (profile.balance or 0) + refund_amount
                        profile.save(update_fields=['balance'])
                    except Exception:
                        pass
                    try:
                        old_ticket.delete()
                    except Exception:
                        pass
            except Exception:
                pass
    except Exception:
        pass

    return render(request, "payment_success.html", {"ticket": ticket, "processed": processed})


@login_required
def payment_cancel(request):
    return render(request, "payment_cancel.html")


def kvitokindex(request):
    """Render ticket search page and provide featured routes from DB.

    Featured routes are built from active `Trip` objects (upcoming first).
    Each item contains `image_url`, `from_city`, `to_city`, `price`, `currency`, `url`, `promo`.
    Clicking a card links back to this page with query params so the search runs automatically.
    """
    from django.templatetags.static import static
    from datetime import date, datetime
    from urllib.parse import quote_plus
    import os
    import re as _re

    today = date.today()
    # If the user supplied a specific date, only show trips that run on that date
    date_query = request.GET.get('date')
    date_obj = None
    if date_query:
        try:
            date_obj = datetime.fromisoformat(date_query).date()
        except Exception:
            try:
                date_obj = datetime.strptime(date_query, '%d.%m.%Y').date()
            except Exception:
                date_obj = None

    trips_qs = Trip.objects.filter(active=True)
    if date_obj:
        unavail_qs = TripDayAvailability.objects.filter(trip=OuterRef('pk'), date=date_obj, available=False)
        avail_qs = TripDayAvailability.objects.filter(trip=OuterRef('pk'), date=date_obj, available=True)
        trips_qs = trips_qs.annotate(unavailable_on_date=Exists(unavail_qs), available_on_date=Exists(avail_qs))
        trips_qs = trips_qs.filter(unavailable_on_date=False).filter(Q(date__isnull=True) | Q(date=date_obj) | Q(available_on_date=True))
    else:
        # prefer upcoming dated trips but still include recurring trips (date is NULL)
        # Also include trips that have a future TripDayAvailability record marked available=True
        if Trip.objects.filter(date__isnull=False).exists():
            trips_qs = trips_qs.filter(
                Q(date__isnull=True) | Q(date__gte=today) | Q(day_availabilities__date__gte=today, day_availabilities__available=True)
            ).distinct()
    trips = trips_qs.order_by('date')[:8]

    featured = []
    static_img_dir = os.path.join(os.getcwd(), 'project', 'static', 'img')

    for trip in trips:
        # determine endpoints
        start = trip.start_city.name if trip.start_city else None
        end = trip.end_city.name if trip.end_city else None
        if (not start or not end) and trip.trip_stops.exists():
            stops = list(trip.trip_stops.order_by('order').select_related('city'))
            if stops:
                if not start:
                    start = stops[0].city.name
                if not end:
                    end = stops[-1].city.name

        start = start or ''
        end = end or ''

        price = float(trip.base_price or 0.0)
        currency = trip.currency or 'UAH'

        # try to pick an image file based on route name (user can add images later)
        slug = _re.sub(r'[^0-9a-zA-Zа-яА-ЯіІїЇєЄёЁґҐ]+', '_', (trip.route.name or 'route')).lower()
        candidate = f"{slug}.jpg"
        if os.path.exists(os.path.join(static_img_dir, candidate)):
            image_url = f"/static/img/{candidate}"
        else:
            # fallback to bundled hero image
            image_url = static('img/5269672762366693828.jpg')

        d = trip.date.isoformat() if trip.date else ''
        url = f"/kvitokindex/?from={quote_plus(start)}&to={quote_plus(end)}"
        if d:
            url += f"&date={d}"

        # format price display with comma decimal separator and currency symbol
        try:
            price_str = "{:.2f}".format(price).replace('.', ',')
        except Exception:
            price_str = str(price)
        symbol = '₴' if (currency or '').upper() == 'UAH' else ('zł' if (currency or '').upper() == 'PLN' else (currency or ''))
        price_display = f"Від {price_str} {symbol}" if price_str else ''

        featured.append({
            'image_url': image_url,
            'from_city': start,
            'to_city': end,
            'price': f"{price:.2f}",
            'trip_id': trip.id,
            'price_display': price_display,
            'currency': currency,
            'url': url,
            'promo': bool(getattr(trip, 'discount_percent', 0))
        })

    # If staff user, include recent support tickets for quick access
    admin_support_tickets = []
    if request.user.is_authenticated and request.user.is_staff:
        try:
            admin_support_tickets = SupportTicket.objects.filter(is_archived=False).order_by('-last_message_at', '-created_at')[:10]
        except Exception:
            admin_support_tickets = []

    return render(request, "kvitokindex.html", {'featured_routes': featured, 'admin_support_tickets': admin_support_tickets})


@login_required
def create_ticket(request):
    if request.method == "POST":
        from_city = request.POST.get("from_city") or request.POST.get("from") or ''
        to_city = request.POST.get("to_city") or request.POST.get("to") or ''
        passengers = int(request.POST.get("passengers") or 1)
        travel_date = request.POST.get("travel_date") or request.POST.get("date") or ''
        price = int(request.POST.get("price") or 0)  # за 1 квиток

        ticket = Ticket.objects.create(
            user=request.user,
            from_city=from_city,
            to_city=to_city,
            travel_date=travel_date,
            passengers=passengers,
            total_price=price * passengers,
            currency='UAH',
            discount_percent=0,
            contact_email=(request.POST.get('email') or request.user.email),
            contact_phone=(request.POST.get('phone') or '')
        )
        try:
            request.session['last_ticket_id'] = ticket.id
        except Exception:
            pass

        payment = Payment.objects.create(
            ticket=ticket,
            user=request.user,
            provider='wayforpay',
            amount=(price * passengers),
            currency='UAH',
            status='pending'
        )

        return _render_wayforpay_form(request, ticket, payment, contact_email=(request.POST.get('email') or request.user.email), contact_phone=(request.POST.get('phone') or ''))

    return redirect("main:kvitokindex")


def oplata(request):
    """Render payment/thank-you page and show featured DB routes below the content."""
    from django.templatetags.static import static
    from datetime import date
    from urllib.parse import quote_plus
    import os
    import re as _re

    today = date.today()
    trips_qs = Trip.objects.filter(active=True)
    # prefer upcoming dated trips but include recurring trips (date is NULL)
    # Also include trips that have a future TripDayAvailability record marked available=True
    if Trip.objects.filter(date__isnull=False).exists():
        trips_qs = trips_qs.filter(
            Q(date__isnull=True) | Q(date__gte=today) | Q(day_availabilities__date__gte=today, day_availabilities__available=True)
        ).distinct()
    trips = trips_qs.order_by('date')[:8]

    featured = []
    static_img_dir = os.path.join(os.getcwd(), 'project', 'static', 'img')

    for trip in trips:
        start = trip.start_city.name if trip.start_city else None
        end = trip.end_city.name if trip.end_city else None
        if (not start or not end) and trip.trip_stops.exists():
            stops = list(trip.trip_stops.order_by('order').select_related('city'))
            if stops:
                if not start:
                    start = stops[0].city.name
                if not end:
                    end = stops[-1].city.name

        start = start or ''
        end = end or ''

        price = float(trip.base_price or 0.0)
        currency = trip.currency or 'UAH'

        slug = _re.sub(r'[^0-9a-zA-Zа-яА-ЯіІїЇєЄёЁґҐ]+', '_', (trip.route.name or 'route')).lower()
        candidate = f"{slug}.jpg"
        if os.path.exists(os.path.join(static_img_dir, candidate)):
            image_url = f"/static/img/{candidate}"
        else:
            image_url = static('img/5269672762366693828.jpg')

        try:
            price_str = "{:.2f}".format(price).replace('.', ',')
        except Exception:
            price_str = str(price)
        symbol = '₴' if (currency or '').upper() == 'UAH' else ('zł' if (currency or '').upper() == 'PLN' else (currency or ''))
        price_display = f"Від {price_str} {symbol}" if price_str else ''

        d = trip.date.isoformat() if trip.date else ''
        url = f"/kvitokindex/?from={quote_plus(start)}&to={quote_plus(end)}"
        if d:
            url += f"&date={d}"

        featured.append({
            'image_url': image_url,
            'from_city': start,
            'to_city': end,
            'price': f"{price:.2f}",
            'trip_id': trip.id,
            'price_display': price_display,
            'currency': currency,
            'url': url,
            'promo': bool(getattr(trip, 'discount_percent', 0))
        })
    return render(request, "oplata.html", {'featured_routes': featured})


@login_required
def buy_trip(request, trip_id):
    """Redirect legacy buy links to the checkout page for WayForPay."""
    params = {}
    for key in ('pax', 'date', 'travel_date', 'from', 'to', 'email', 'phone', 'exchange_ticket'):
        value = request.GET.get(key)
        if value:
            params[key] = value
    url = reverse('main:checkout', args=[trip_id])
    if params:
        url = f"{url}?{urlencode(params)}"
    return redirect(url)


@login_required
def checkout_root(request):
    """Fallback handler for requests to /checkout/ without trip_id.

    Accepts query params: `trip`, `trip_id`, or `id` (preferred), `pax`, `date`.
    If a trip id is provided, redirect to the canonical `/checkout/<id>/` URL
    preserving `pax` and `date`. Otherwise redirect to the ticket search page.
    """
    trip_id = request.GET.get('trip') or request.GET.get('trip_id') or request.GET.get('id')
    pax = request.GET.get('pax')
    date = request.GET.get('date') or request.GET.get('travel_date')

    if trip_id:
        try:
            tid = int(trip_id)
        except Exception:
            return redirect('main:kvitokindex')
        url = reverse('main:checkout', args=[tid])
        params = []
        if pax:
            params.append(f"pax={pax}")
        if date:
            params.append(f"date={date}")
        if params:
            url = url + '?' + '&'.join(params)
        return redirect(url)

    # No trip id — fall back to search page
    return redirect('main:kvitokindex')


@login_required
@ensure_csrf_cookie
@csrf_protect
def checkout(request, trip_id):
    """Checkout + WayForPay"""

    from datetime import date, datetime

    # Checkout flow: render form (GET) or create Ticket + Payment and render WayForPay form (POST)

    # -----------------------------
    # GET TRIP
    # -----------------------------
    try:
        trip = Trip.objects.get(pk=trip_id, active=True)
    except Trip.DoesNotExist:
        messages.error(request, "Рейс не знайдено")
        return redirect('main:kvitokindex')

    # -----------------------------
    # PASSENGERS COUNT
    # -----------------------------
    pax = int(
        request.GET.get('pax')
        or request.POST.get('passengers')
        or 1
    )

    # passengers count parsed above

    # -----------------------------
    # TRAVEL DATE
    # -----------------------------
    date_str = (
        request.GET.get('date')
        or request.GET.get('travel_date')
        or request.POST.get('date')
        or request.POST.get('travel_date')
    )


    travel_date = None

    if date_str:
        try:
            travel_date = datetime.fromisoformat(date_str).date()
        except Exception as e:
            print("ISO PARSE ERROR:", e)

            try:
                travel_date = datetime.strptime(date_str, '%d.%m.%Y').date()

            except Exception as e:
                print("UA DATE PARSE ERROR:", e)

    # fallback
    if not travel_date:
        travel_date = trip.date or date.today()

    # -----------------------------
    # PRICE
    # -----------------------------
    # Accept optional selected segment from/to (passed from search results)
    selected_from = (request.GET.get('from') or request.POST.get('from') or '').strip()
    selected_to = (request.GET.get('to') or request.POST.get('to') or '').strip()

    trip_stops = list(trip.trip_stops.select_related('city').all())

    try:
        # If user selected a specific segment (from/to), try to compute price for that segment
        price = None
        explicit_fare = None

        def find_stop_by_name(name):
            if not name:
                return None
            lname = name.strip().lower()
            # exact city name match first
            for s in trip_stops:
                try:
                    if s.city and s.city.name and s.city.name.strip().lower() == lname:
                        return s
                except Exception:
                    continue
            # partial match fallback
            for s in trip_stops:
                try:
                    if s.city and s.city.name and lname in s.city.name.strip().lower():
                        return s
                except Exception:
                    continue
            return None

        from_stop = find_stop_by_name(selected_from)
        to_stop = find_stop_by_name(selected_to)

        # Try explicit TripFare lookup for the exact pair
        if from_stop and to_stop:
            explicit_fare = find_trip_fare(trip, getattr(from_stop, 'city', None), getattr(to_stop, 'city', None))

            if explicit_fare and getattr(explicit_fare, 'price', None) is not None and float(explicit_fare.price or 0) > 0:
                price = float(explicit_fare.price)
            else:
                # compute sum of leg prices between the two stops
                try:
                    fo = getattr(from_stop, 'order', None)
                    to = getattr(to_stop, 'order', None)
                    if fo is not None and to is not None:
                        lo = min(fo, to)
                        hi = max(fo, to)
                        price_sum = 0.0
                        missing_price = False
                        for s in trip_stops:
                            so = getattr(s, 'order', None)
                            if so is None:
                                continue
                            if so >= lo and so < hi:
                                p = float(getattr(s, 'price', 0.0) or 0.0)
                                if p <= 0:
                                    missing_price = True
                                price_sum += p
                        if price_sum > 0 and not missing_price:
                            price = price_sum
                except Exception:
                    price = None

        # fallback: sum all stop prices or use base_price
        if price is None:
            price_sum = 0
            for stop in trip_stops:
                stop_price = float(stop.price or 0)
                price_sum += stop_price

            if price_sum > 0:
                price = price_sum
            else:
                price = float(trip.base_price or 0)

    except Exception as e:
        print("PRICE ERROR:", e)
        price = float(trip.base_price or 0)

    # Apply trip-level discount (percent) if present
    try:
        discount_percent = int(getattr(trip, 'discount_percent', 0) or 0)
    except Exception:
        discount_percent = 0
    price_after = round(price * (1.0 - (discount_percent / 100.0)), 2) if discount_percent else round(price, 2)
    total = round(price_after * pax, 2)

    # price and total computed above
    # Exchange handling: apply credit from an existing ticket when rebooking
    exchange_old_ticket = None
    exchange_credit = 0.0
    net_total = total
    try:
        exch_param = request.GET.get('exchange_ticket') or request.POST.get('exchange_ticket')
        if exch_param:
            try:
                exch_id = int(exch_param)
                old = Ticket.objects.filter(pk=exch_id).first()
                if old and getattr(old, 'paid', False) and getattr(old, 'user_id', None) == getattr(request.user, 'id', None):
                    exchange_old_ticket = old
                    exchange_credit = float(old.total_price or 0.0)
                    net_total = round(max(0.0, total - exchange_credit), 2)
            except Exception:
                pass
    except Exception:
        exchange_credit = 0.0
        net_total = total

    # normalize display names for the selected segment
    try:
        display_from = selected_from or (from_stop.city.name if 'from_stop' in locals() and getattr(from_stop, 'city', None) else (trip.start_city.name if trip.start_city else ''))
    except Exception:
        display_from = selected_from or (trip.start_city.name if trip.start_city else '')
    try:
        display_to = selected_to or (to_stop.city.name if 'to_stop' in locals() and getattr(to_stop, 'city', None) else (trip.end_city.name if trip.end_city else ''))
    except Exception:
        display_to = selected_to or (trip.end_city.name if trip.end_city else '')

    # -----------------------------
    # DATE CHECK
    # -----------------------------
    # validate selected travel_date is not in the past

    if travel_date < date.today():
        # past date

        messages.error(
            request,
            'Цей рейс вже відправився'
        )

        return redirect('main:kvitokindex')

    # -----------------------------
    # DEPARTURE TIME CHECK
    # -----------------------------
    first_stop = trip.trip_stops.order_by('order').first()

    if first_stop and first_stop.departure_time:

        dep_naive = datetime.combine(
            travel_date,
            first_stop.departure_time
        )

        try:
            dep_dt = timezone.make_aware(
                dep_naive,
                timezone.get_current_timezone()
            )
        except Exception:
            dep_dt = dep_naive

        # departure time check

        if timezone.now() >= dep_dt:

            # already departed

            messages.error(
                request,
                'Рейс вже відправився'
            )

            return redirect('main:kvitokindex')

    # -----------------------------
    # AVAILABILITY CHECK
    # -----------------------------
    try:
        available = trip.is_available_on(travel_date)


        if not available:

            # not available on date

            messages.error(
                request,
                'Рейс недоступний на цю дату'
            )

            return redirect('main:kvitokindex')

    except Exception as e:
        print("AVAILABILITY ERROR:", e)

    # -----------------------------
    # GET REQUEST
    # -----------------------------
    # determine display currency (prefer explicit fare currency if present)
    try:
        display_currency = (explicit_fare.currency if 'explicit_fare' in locals() and getattr(explicit_fare, 'currency', None) else (trip.currency or 'UAH'))
    except Exception:
        display_currency = (trip.currency or 'UAH')

    if request.method == 'GET':
        # Direct checkout for logged-in users with saved phone: skip the form and
        # create the ticket/payment immediately, then render the WayForPay auto-submit page.
        profile_phone = ''
        try:
            from .models import Profile
            profile = Profile.objects.filter(user=request.user).first()
            if profile:
                profile_phone = (getattr(profile, 'phone', '') or '').strip()
        except Exception:
            profile_phone = ''

        if profile_phone:
            contact_email = (request.GET.get('email') or request.user.email or '').strip()
            contact_phone = request.GET.get('phone') or profile_phone
            posted_from = display_from
            posted_to = display_to
            try:
                ticket = Ticket.objects.create(
                    user=request.user,
                    trip=trip,
                    from_city=(posted_from or (trip.start_city.name if trip.start_city else '')),
                    to_city=(posted_to or (trip.end_city.name if trip.end_city else '')),
                    travel_date=travel_date,
                    passengers=pax,
                    total_price=net_total,
                    currency=(trip.currency or 'UAH'),
                    discount_percent=discount_percent,
                    paid=False,
                    contact_email=contact_email,
                    contact_phone=contact_phone,
                    route=(trip.route.name if trip.route else '')
                )
                request.session['last_ticket_id'] = ticket.id
            except Exception:
                ticket = None

            if ticket:
                from .models import Passenger
                for i in range(pax):
                    Passenger.objects.create(
                        ticket=ticket,
                        first_name='-',
                        last_name=''
                    )
                from .models import Payment
                payment = Payment.objects.create(
                    ticket=ticket,
                    user=request.user,
                    provider='wayforpay',
                    amount=net_total,
                    currency=(display_currency or trip.currency or 'UAH'),
                    status='pending'
                )
                if net_total <= 0:
                    try:
                        payment.status = 'success'
                        payment.save(update_fields=['status'])
                        ticket.paid = True
                        ticket.save(update_fields=['paid'])
                        _send_ticket_email(ticket, payment)
                    except Exception:
                        pass
                    try:
                        request.session.pop('last_ticket_id', None)
                    except Exception:
                        pass
                    return render(request, 'payment_success.html', {'ticket': ticket, 'processed': True})

                return _render_wayforpay_form(request, ticket, payment, contact_email=contact_email, contact_phone=contact_phone)

        get_token(request)
        return render(request, 'checkout.html', {
            'trip': trip,
            'pax': pax,
            'price_per_ticket': price_after,
            'total': total,
            'discount_percent': discount_percent,
            'user_email': request.user.email,
            'travel_date': travel_date,
            'selected_from': display_from,
            'selected_to': display_to,
            'display_currency': display_currency,
        })

    # -----------------------------
    # POST REQUEST
    # -----------------------------
    # Handle POST: create ticket, passengers and payment

    first_names = request.POST.getlist('passenger_first') or []
    last_names = request.POST.getlist('passenger_last') or []

    contact_email = (
        request.POST.get('email')
        or request.user.email
    )

    contact_phone = (
        request.POST.get('phone')
        or ''
    ).strip()

    if not contact_phone:
        messages.error(request, 'Будь ласка, вкажіть номер телефону для оформлення квитка')
        return render(request, 'checkout.html', {
            'trip': trip,
            'pax': pax,
            'price_per_ticket': price_after,
            'total': total,
            'discount_percent': discount_percent,
            'user_email': contact_email,
            'travel_date': travel_date,
            'selected_from': display_from,
            'selected_to': display_to,
            'display_currency': display_currency,
        })

    # -----------------------------
    # CREATE TICKET
    # -----------------------------
    # Use posted selected from/to for the ticket so the document matches user choice
    posted_from = (request.POST.get('from') or request.POST.get('selected_from') or display_from)
    posted_to = (request.POST.get('to') or request.POST.get('selected_to') or display_to)

    # Seats availability check (prevent oversell)
    try:
        sold = Ticket.objects.filter(trip=trip, travel_date=travel_date).aggregate(total=Sum('passengers'))['total'] or 0
        seats_left = (trip.seats or 0) - int(sold or 0)
        if seats_left < pax:
            messages.error(request, 'Недостатньо вільних місць на цей рейс')
            return redirect('main:kvitokindex')
    except Exception:
        pass

    ticket = Ticket.objects.create(
        user=request.user,
        trip=trip,
        from_city=(posted_from or (trip.start_city.name if trip.start_city else '')),
        to_city=(posted_to or (trip.end_city.name if trip.end_city else '')),
        travel_date=travel_date,
        passengers=pax,
        total_price=net_total,
        currency=(trip.currency or 'UAH'),
        discount_percent=discount_percent,
            paid=False,
            contact_email=contact_email,
            contact_phone=contact_phone,
        route=(
            trip.route.name
            if trip.route else ''
        )
    )
    try:
        request.session['last_ticket_id'] = ticket.id
        try:
            exch = request.POST.get('exchange_ticket') or request.GET.get('exchange_ticket')
            if exch:
                request.session['exchange_old_ticket_id'] = int(exch)
        except Exception:
            pass
    except Exception:
        pass

    # ticket created

    # -----------------------------
    # SAVE PASSENGERS
    # -----------------------------
    from .models import Passenger

    for i in range(pax):

        fn = first_names[i] if i < len(first_names) else ''
        ln = last_names[i] if i < len(last_names) else ''

        Passenger.objects.create(
            ticket=ticket,
            first_name=fn or '-',
            last_name=ln or ''
        )

    # passengers saved

    # -----------------------------
    # CREATE PAYMENT
    # -----------------------------
    from .models import Payment

    # Apply profile balance (site credit) if available
    credit_applied = 0.0
    try:
        profile = getattr(request.user, 'profile', None)
        if profile:
            try:
                profile_balance = float(profile.balance or 0.0)
            except Exception:
                profile_balance = 0.0
            if profile_balance > 0 and net_total > 0:
                credit_applied = round(min(profile_balance, net_total), 2)
                net_total = round(max(0.0, net_total - credit_applied), 2)
                try:
                    Payment.objects.create(
                        ticket=ticket,
                        user=request.user,
                        amount=-abs(credit_applied),
                        currency=(display_currency or trip.currency or 'UAH'),
                        status='success',
                        provider='credit',
                        data={'by': 'balance_usage', 'prev_balance': profile_balance}
                    )
                except Exception:
                    logging.exception('Failed to create internal credit payment for ticket %s', getattr(ticket, 'id', None))
                try:
                    profile.balance = (profile_balance - credit_applied)
                    profile.save(update_fields=['balance'])
                except Exception:
                    logging.exception('Failed to deduct profile.balance for user %s', getattr(request.user, 'id', None))
    except Exception:
        credit_applied = 0.0

    payment = Payment.objects.create(
        ticket=ticket,
        user=request.user,
        provider='wayforpay',
        amount=net_total,
        currency=(display_currency or trip.currency or 'UAH'),
        status='pending'
    )
    # payment created

    # If net_total is zero (old ticket fully covers the new one), mark payment
    # and ticket as paid immediately and apply exchange credit.
    if net_total <= 0:
        try:
            payment.status = 'success'
            payment.save(update_fields=['status'])
        except Exception:
            pass
        try:
            ticket.paid = True
            ticket.save(update_fields=['paid'])
        except Exception:
            pass
        try:
            _send_ticket_email(ticket, payment)
        except Exception:
            pass

        try:
            exch_old_id = None
            try:
                exch_old_id = request.session.pop('exchange_old_ticket_id', None)
            except Exception:
                exch_old_id = None
            if not exch_old_id and exchange_old_ticket:
                exch_old_id = exchange_old_ticket.id
            if exch_old_id:
                old_ticket = Ticket.objects.filter(pk=int(exch_old_id)).first()
                if old_ticket:
                    refund_amount = float(old_ticket.total_price or 0.0)
                    try:
                        Payment.objects.create(ticket=None, user=old_ticket.user, amount=-abs(refund_amount), currency=(old_ticket.currency or 'UAH'), status='refunded', provider='exchange', data={'by': request.user.username if request.user.is_authenticated else 'system', 'old_ticket': old_ticket.id, 'new_ticket': ticket.id})
                    except Exception:
                        pass
                    try:
                        profile = old_ticket.user.profile
                        profile.balance = (profile.balance or 0) + refund_amount
                        profile.save(update_fields=['balance'])
                    except Exception:
                        pass
                    try:
                        old_ticket.delete()
                    except Exception:
                        pass
        except Exception:
            pass

        try:
            request.session.pop('last_ticket_id', None)
        except Exception:
            pass

        return render(request, 'payment_success.html', { 'ticket': ticket, 'processed': True })

    # -----------------------------
    # WAYFORPAY
    # -----------------------------
    return _render_wayforpay_form(request, ticket, payment, contact_email=contact_email, contact_phone=contact_phone)


def _get_carrier_business_details(carrier):
    business_number = (getattr(carrier, 'business_number', '') or '').strip()
    business_type = (getattr(carrier, 'business_type', '') or '').strip()

    if business_type == 'FOP':
        return 'ФОП', business_number
    if business_type == 'TOV':
        return 'ТОВ', business_number

    normalized = business_number.lower()
    if normalized.startswith('тов'):
        return 'ТОВ', business_number
    if normalized.startswith('фоп'):
        return 'ФОП', business_number

    return 'ФОП/ТОВ', business_number


def _generate_ticket_pdf_bytes(ticket, request=None):
    """Return PDF bytes for a Ticket using reportlab.

    The PDF includes carrier information, but does not embed a QR code.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
        from reportlab.lib import colors
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.graphics.shapes import Drawing
    except Exception:
        return None

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    carrier_name = ''
    carrier_business_label = ''
    carrier_business_number = ''
    carrier_phone = ''
    try:
        if getattr(ticket, 'trip', None):
            if getattr(ticket.trip, 'carrier_user', None):
                carrier = getattr(ticket.trip.carrier_user, 'carrier_account', None)
                if carrier:
                    carrier_name = carrier.company_name or carrier.username or ticket.trip.carrier or ''
                    carrier_business_label, carrier_business_number = _get_carrier_business_details(carrier)
                    carrier_phone = carrier.phone or ''
    except Exception:
        pass
    if not carrier_name:
        carrier_name = ticket.trip.carrier if getattr(ticket, 'trip', None) else ''

    # Try to register a TTF font for Cyrillic if available (Windows or common Linux paths)
    regular_font = 'Helvetica'
    bold_font = 'Helvetica-Bold'
    try:
        # Windows fonts (if running on Windows)
        if os.path.exists(r'C:\Windows\Fonts\arial.ttf'):
            pdfmetrics.registerFont(TTFont('Arial', r'C:\Windows\Fonts\arial.ttf'))
            regular_font = 'Arial'
        if os.path.exists(r'C:\Windows\Fonts\arialbd.ttf'):
            pdfmetrics.registerFont(TTFont('Arial-Bold', r'C:\Windows\Fonts\arialbd.ttf'))
            bold_font = 'Arial-Bold'

        # Common Linux fonts (DejaVu / Noto) often present on Linux containers (Render, Docker)
        try:
            linux_candidates = [
                '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
                '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
                '/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf',
                '/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf',
                '/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf',
                '/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf',
            ]
            for p in linux_candidates:
                if os.path.exists(p):
                    name = os.path.splitext(os.path.basename(p))[0]
                    try:
                        pdfmetrics.registerFont(TTFont(name, p))
                        # prefer explicit DejaVu/Noto names for regular/bold mapping
                        if 'bold' in name.lower() or 'b' in name.lower() and name.lower().endswith('b'):
                            bold_font = name
                        else:
                            regular_font = name
                    except Exception:
                        pass

        except Exception:
            pass

        # Also accept project-provided fonts under static/fonts (if any)
        try:
            fonts_dir = os.path.join(settings.BASE_DIR, 'static', 'fonts')
            reg = os.path.join(fonts_dir, 'DejaVuSans.ttf')
            reg_b = os.path.join(fonts_dir, 'DejaVuSans-Bold.ttf')
            if os.path.exists(reg):
                try:
                    pdfmetrics.registerFont(TTFont('DejaVuSans', reg))
                    regular_font = 'DejaVuSans'
                except Exception:
                    pass
            if os.path.exists(reg_b):
                try:
                    pdfmetrics.registerFont(TTFont('DejaVuSans-Bold', reg_b))
                    bold_font = 'DejaVuSans-Bold'
                except Exception:
                    pass
        except Exception:
            pass
    except Exception:
        pass

    margin = 12 * mm
    # Attempt to use a background ticket template image if available
    template_path = None
    try:
        candidates = [
            os.path.join(settings.BASE_DIR, 'static', 'img', 'ticket_template.png'),
            os.path.join(settings.BASE_DIR, 'static', 'img', 'ticket_template.jpg'),
            os.path.join(settings.BASE_DIR, 'static', 'img', 'ticket_template.jpeg'),
            # also accept the exact filename user provided
            os.path.join(settings.BASE_DIR, 'static', 'img', '5474563455667868582.jpg'),
            os.path.join(settings.BASE_DIR, 'static', 'img', '5474563455667868582.png'),
        ]
        for p in candidates:
            if os.path.exists(p):
                template_path = p
                break
    except Exception:
        template_path = None

    # User requested not to include the background photo — disable template usage.
    template_path = None

    if template_path:
        try:
            from reportlab.lib.utils import ImageReader
            img = ImageReader(template_path)
            iw, ih = img.getSize()
            draw_w = width - margin * 2
            scale = draw_w / float(iw)
            draw_h = float(ih) * scale
            img_x = margin
            img_y = height - margin - draw_h
            c.drawImage(img, img_x, img_y, width=draw_w, height=draw_h)

            # Mask prototype/unused areas conservatively
            try:
                c.saveState()
                c.setFillColor(colors.white)
                # small top banner and prototype lines
                c.rect(img_x + draw_w * 0.02, img_y + draw_h - draw_h * 0.14, draw_w * 0.96, draw_h * 0.10, fill=1, stroke=0)
                # right side area (baggage/insurance) near bottom of template
                c.rect(img_x + draw_w * 0.60, img_y + draw_h * 0.04, draw_w * 0.36, draw_h * 0.22, fill=1, stroke=0)
                c.restoreState()
            except Exception:
                pass

            # Draw company header and ticket number (anchored to template top)
            try:
                c.setFillColor(colors.black)
                try:
                    c.setFont(bold_font, 20)
                except Exception:
                    c.setFont('Helvetica-Bold', 20)
                c.drawString(img_x + 8, img_y + draw_h - 28, 'Dieller Bus')
                try:
                    c.setFont(regular_font, 9)
                except Exception:
                    c.setFont('Helvetica', 9)
                c.drawString(img_x + 8, img_y + draw_h - 44, 'Квиток на проїзд — офіційний документ')

                try:
                    c.setFont(bold_font, 32)
                except Exception:
                    c.setFont('Helvetica-Bold', 32)
                c.drawCentredString(img_x + draw_w / 2, img_y + draw_h - 36, f"№ {ticket.id}")
            except Exception:
                pass

            # Draw top info box with route on left and QR on right
            try:
                box_h = draw_h * 0.16
                box_x = img_x + draw_w * 0.02
                box_w = draw_w * 0.96
                box_y = img_y + draw_h - box_h - draw_h * 0.12
                c.setStrokeColor(colors.black)
                c.rect(box_x, box_y, box_w, box_h, stroke=1, fill=0)

                # route and small details
                left_x = box_x + 10
                left_top = box_y + box_h - 12
                try:
                    c.setFont(bold_font, 14)
                except Exception:
                    c.setFont('Helvetica-Bold', 14)
                c.drawString(left_x, left_top, f"{ticket.from_city} → {ticket.to_city}")
                try:
                    c.setFont(regular_font, 10)
                except Exception:
                    c.setFont('Helvetica', 10)
                date_str = ticket.travel_date.strftime('%d.%m.%Y') if getattr(ticket, 'travel_date', None) else ''
                c.drawString(left_x, left_top - 16, f"Дата: {date_str}")
                c.drawString(left_x, left_top - 30, f"Пасажирів: {ticket.passengers}")

                # Carrier details in the info box
                try:
                    info_y = left_top - 46
                    if carrier_name:
                        c.drawString(left_x, info_y, f"Перевізник: {carrier_name}")
                        info_y -= 12
                    if carrier_business_number:
                        c.drawString(left_x, info_y, f"ФОП/ТОВ: {carrier_business_number}")
                        info_y -= 12
                    if carrier_phone:
                        c.drawString(left_x, info_y, f"Телефон: {carrier_phone}")
                        info_y -= 12
                except Exception:
                    pass

            except Exception:
                pass

            # Passenger list below the box
            try:
                list_x = box_x
                list_y = box_y - 18
                try:
                    c.setFont(bold_font, 12)
                except Exception:
                    c.setFont('Helvetica-Bold', 12)
                c.drawString(list_x, list_y, 'Пасажири:')
                pax_y = list_y - 16
                try:
                    c.setFont(regular_font, 11)
                except Exception:
                    c.setFont('Helvetica', 11)
                for p in ticket.passenger_set.all():
                    if pax_y < img_y + 20:
                        break
                    c.drawString(list_x + 8, pax_y, f"- {p.first_name} {p.last_name}")
                    pax_y -= 14
            except Exception:
                pass

            # Watermark across page
            try:
                c.saveState()
                c.setFillColorRGB(0.92, 0.92, 0.92)
                try:
                    c.setFont(bold_font, 60)
                except Exception:
                    c.setFont('Helvetica-Bold', 60)
                c.translate(width / 2, height / 2)
                c.rotate(30)
                c.drawCentredString(0, 0, 'DIELLER BUS — ОРИГІНАЛ')
                c.restoreState()
            except Exception:
                pass

            # Paid / reservation stamp at bottom-right (outside template area)
            try:
                stamp_text = 'ОПЛАЧЕНО' if getattr(ticket, 'paid', False) else 'РЕЗЕРВАЦІЯ'
                stamp_color = colors.green if getattr(ticket, 'paid', False) else colors.orange
                sw = 120
                sh = 26
                c.setFillColor(stamp_color)
                c.rect(width - margin - sw, 36, sw, sh, fill=1, stroke=0)
                c.setFillColor(colors.white)
                try:
                    c.setFont(bold_font, 12)
                except Exception:
                    c.setFont('Helvetica-Bold', 12)
                c.drawCentredString(width - margin - sw / 2, 44, stamp_text)
            except Exception:
                pass

        except Exception:
            template_path = None

    if not template_path:
        # Header — compute top baseline and place title + ticket number safely
        top_y = height - margin - (6 * mm)
        try:
            c.setFont(bold_font, 28)
        except Exception:
            c.setFont('Helvetica-Bold', 28)
        c.setFillColor(colors.black)
        c.drawString(margin, top_y, 'Dieller Bus')

        # Ticket number on the right to avoid centering overlap
        try:
            c.setFont(bold_font, 32)
        except Exception:
            c.setFont('Helvetica-Bold', 32)
        c.drawRightString(width - margin, top_y, f"№ {ticket.id}")

        # Legal phrase below header
        try:
            c.setFont(regular_font, 10)
        except Exception:
            c.setFont('Helvetica', 10)
        legal = 'Квиток на проїзд пасажира автомобільним транспортом на приміських автобусних маршрутах загального користування'
        legal_y = top_y - (8 * mm)
        # short-wrap legal text if too long
        try:
            if pdfmetrics.stringWidth(legal, regular_font, 10) > (width - margin * 2):
                # split into two lines roughly
                mid = len(legal) // 2
                left = legal[:mid].rsplit(' ', 1)[0]
                right = legal[len(left):].strip()
                c.drawString(margin, legal_y, left)
                c.drawString(margin, legal_y - 12, right)
                box_top_anchor = legal_y - 28
            else:
                c.drawString(margin, legal_y, legal)
                box_top_anchor = legal_y - 16
        except Exception:
            c.drawString(margin, legal_y, legal)
            box_top_anchor = legal_y - 16

        # Attempt to discover departure/arrival times and carrier (best-effort)
        depart_time = ''
        arrive_time = ''
        carrier_name = (ticket.route or '')
        try:
            if getattr(ticket, 'travel_date', None) and ticket.from_city and ticket.to_city:
                qs = Trip.objects.filter(start_city__name__iexact=(ticket.from_city or ''), end_city__name__iexact=(ticket.to_city or ''))
                try:
                    qs_date = qs.filter(date=ticket.travel_date)
                    if qs_date.exists():
                        qs = qs_date
                except Exception:
                    pass
                if qs.exists():
                    trip = qs.first()
                    carrier_name = (trip.carrier_user.username if getattr(trip, 'carrier_user', None) else (trip.carrier or (trip.title or (trip.route.name if trip.route else ''))))
                    stops = list(trip.trip_stops.order_by('order'))
                    if stops:
                        first = stops[0]
                        last = stops[-1]
                        try:
                            if getattr(first, 'departure_time', None):
                                depart_time = first.departure_time.strftime('%H:%M')
                        except Exception:
                            depart_time = ''
                        try:
                            if getattr(last, 'arrival_time', None):
                                arrive_time = last.arrival_time.strftime('%H:%M')
                        except Exception:
                            arrive_time = ''
        except Exception:
            pass

        # Price and purchase date
        try:
            total_price = float(ticket.total_price or 0.0)
        except Exception:
            total_price = 0.0
        pax = int(getattr(ticket, 'passengers', 1) or 1)
        try:
            per_price = total_price / pax if pax else total_price
        except Exception:
            per_price = total_price
        currency = getattr(ticket, 'currency', 'UAH') or 'UAH'
        try:
            purchase = ticket.created_at.strftime('%d.%m.%Y %H:%M') if getattr(ticket, 'created_at', None) else ''
        except Exception:
            purchase = ''

        # Route/info box with decorative stripe
        box_h = 120
        box_x = margin
        box_w = width - margin * 2
        # place box below legal text using computed anchor
        try:
            box_y = box_top_anchor
        except Exception:
            box_y = height - 140
        box_x = margin
        box_w = width - margin * 2
        stripe_h = 8 * mm
        # outer box
        c.setStrokeColor(colors.black)
        c.setLineWidth(1)
        c.rect(box_x, box_y - box_h, box_w, box_h, stroke=1, fill=0)
        # decorative stripe at top of box
        try:
            stripe_color = colors.HexColor('#0A62A8')
        except Exception:
            stripe_color = colors.darkblue
        c.setFillColor(stripe_color)
        c.rect(box_x, box_y - stripe_h, box_w, stripe_h, fill=1, stroke=0)

        # Left: route + carrier (fit route into box, reduce font if needed)
        left_x = box_x + 12
        # place route baseline a bit below the stripe to avoid overlap
        route_top = box_y - stripe_h - (6 * mm)
        c.setFillColor(colors.black)
        route_text = f"{ticket.from_city or ''} → {ticket.to_city or ''}".strip()
        max_w = box_w - 40
        # choose font size to fit route_text (start smaller to avoid overlap)
        try:
            fs = 22
            while fs > 10 and pdfmetrics.stringWidth(route_text, bold_font, fs) > max_w:
                fs -= 1
        except Exception:
            fs = 16

        if fs <= 12:
            # split into two lines (from / to)
            part_from = (ticket.from_city or '').strip()
            part_to = (ticket.to_city or '').strip()
            try:
                fsa = 16
                while fsa > 10 and pdfmetrics.stringWidth(part_from, bold_font, fsa) > max_w:
                    fsa -= 1
            except Exception:
                fsa = 12
            try:
                fsb = 16
                while fsb > 10 and pdfmetrics.stringWidth(part_to, bold_font, fsb) > max_w:
                    fsb -= 1
            except Exception:
                fsb = 12
            try:
                c.setFont(bold_font, fsa)
            except Exception:
                c.setFont('Helvetica-Bold', fsa)
            c.drawString(left_x, route_top, part_from)
            try:
                c.setFont(bold_font, fsb)
            except Exception:
                c.setFont('Helvetica-Bold', fsb)
            c.drawString(left_x, route_top - (fsa + 4), part_to)
            carrier_y = route_top - (fsa + fsb + 8)
        else:
            try:
                c.setFont(bold_font, fs)
            except Exception:
                c.setFont('Helvetica-Bold', fs)
            c.drawString(left_x, route_top, route_text)
            carrier_y = route_top - (fs + 6)

        # Highlighted date/time boxes (placed below route)
        date_box_w = 140
        date_box_h = 24
        dep_x = left_x
        dep_y = carrier_y - (date_box_h + 10)
        c.setFillColor(colors.HexColor('#F0F4F8'))
        c.roundRect(dep_x, dep_y, date_box_w, date_box_h, 4, fill=1, stroke=0)
        c.setFillColor(colors.black)
        try:
            c.setFont(bold_font, 11)
        except Exception:
            c.setFont('Helvetica-Bold', 11)
        c.drawString(dep_x + 6, dep_y + 6, f"Відправлення: {depart_time}")

        arr_x = dep_x + date_box_w + 12
        arr_y = dep_y
        c.setFillColor(colors.HexColor('#F0F4F8'))
        c.roundRect(arr_x, arr_y, date_box_w, date_box_h, 4, fill=1, stroke=0)
        c.setFillColor(colors.black)
        c.drawString(arr_x + 6, arr_y + 6, f"Прибуття: {arrive_time}")

        # Right: price, travel date and carrier details inside info box (right-aligned)
        try:
            c.setFont(bold_font, 14)
        except Exception:
            c.setFont('Helvetica-Bold', 14)
        price_y = route_top
        c.drawRightString(box_x + box_w - 12, price_y, f"Сума: {total_price:.2f} {currency}")
        try:
            c.setFont(regular_font, 10)
        except Exception:
            c.setFont('Helvetica', 10)
        travel_date_text = ticket.travel_date.strftime('%d.%m.%Y') if getattr(ticket, 'travel_date', None) else ''
        c.drawRightString(box_x + box_w - 12, price_y - 16, f"Дата рейсу: {travel_date_text}")
        c.drawRightString(box_x + box_w - 12, price_y - 32, f"Перевізник: {carrier_name}")
        if carrier_business_number:
            c.drawRightString(box_x + box_w - 12, price_y - 48, f"{carrier_business_label}: {carrier_business_number}")
        if carrier_phone:
            c.drawRightString(box_x + box_w - 12, price_y - 64, f"Телефон: {carrier_phone}")

        try:
            dep_y = carrier_y - (date_box_h + 12)
        except Exception:
            dep_y = box_y - box_h - 10

    # Passenger list
    c.setFont(bold_font, 12)
    list_y = box_y - box_h - 18
    c.drawString(margin, list_y, 'Пасажири:')
    y = list_y - 16
    c.setFont(regular_font, 11)
    for p in ticket.passenger_set.all():
        if y < 80:
            c.showPage()
            y = height - 80
        c.drawString(margin + 8, y, f"- {p.first_name} {p.last_name}")
        y -= 14

    # Watermark
    try:
        c.saveState()
        c.setFillColorRGB(0.92, 0.92, 0.92)
        try:
            c.setFont(bold_font, 60)
        except Exception:
            c.setFont('Helvetica-Bold', 60)
        c.translate(width / 2, height / 2)
        c.rotate(30)
        c.drawCentredString(0, 0, 'DIELLER BUS — ОРИГІНАЛ')
        c.restoreState()
    except Exception:
        pass

    # Paid / reservation stamp at bottom-right
    try:
        stamp_text = 'ОПЛАЧЕНО' if getattr(ticket, 'paid', False) else 'РЕЗЕРВАЦІЯ'
        stamp_color = colors.green if getattr(ticket, 'paid', False) else colors.orange
        sw = 120
        sh = 26
        c.setFillColor(stamp_color)
        c.rect(width - margin - sw, 36, sw, sh, fill=1, stroke=0)
        c.setFillColor(colors.white)
        try:
            c.setFont(bold_font, 12)
        except Exception:
            c.setFont('Helvetica-Bold', 12)
        c.drawCentredString(width - margin - sw / 2, 44, stamp_text)
    except Exception:
        pass

    # Footer: carrier/license info
    try:
        c.setFont(regular_font, 8)
        footer_items = []
        if carrier_name:
            footer_items.append(f"Перевізник: {carrier_name}")
        if carrier_business_number:
            footer_items.append(f"{carrier_business_label}: {carrier_business_number}")
        if carrier_phone:
            footer_items.append(f"Телефон: {carrier_phone}")
        footer_text = ' · '.join(footer_items) if footer_items else 'Перевізник: Dieller Bus'
        c.drawString(margin, 28, footer_text)
    except Exception:
        pass

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.read()


def _send_ticket_email(ticket, payment):
    """Send ticket PDF to the ticket owner's email with attachment."""
    import logging
    logger = logging.getLogger(__name__)
    recipient = (getattr(ticket, 'user', None) and getattr(ticket.user, 'email', None)) or getattr(ticket, 'contact_email', None)
    if not recipient:
        logger.warning('No email address available for ticket %s', getattr(ticket, 'id', None))
        return False
    recipient = recipient.strip()
    if not recipient:
        logger.warning('No email address available for ticket %s', getattr(ticket, 'id', None))
        return False

    pdf_bytes = None
    try:
        pdf_bytes = _generate_ticket_pdf_bytes(ticket)
    except Exception:
        logger.exception('Error generating PDF for ticket %s', getattr(ticket, 'id', None))

    subject = f"Ваш квиток #{ticket.id} — {ticket.from_city} → {ticket.to_city}"
    body = f"Доброго дня!\n\nДодано квиток #{ticket.id}. Дякуємо за оплату.\n\nЗ повагою, Dieller Bus"
    msg = EmailMessage(subject, body, settings.DEFAULT_FROM_EMAIL, [recipient])

    # Validate generated bytes look like a PDF before attaching
    try:
        if pdf_bytes and isinstance(pdf_bytes, (bytes, bytearray)) and pdf_bytes.strip().startswith(b'%PDF'):
            try:
                msg.attach(f"ticket_{ticket.id}.pdf", pdf_bytes, 'application/pdf')
            except Exception:
                logger.exception('Failed to attach PDF bytes for ticket %s', ticket.id)
        else:
            # Try regenerating once without request context
            logger.warning('Generated PDF invalid or empty for ticket %s, retrying', ticket.id)
            try:
                pdf_bytes = _generate_ticket_pdf_bytes(ticket, None)
                if pdf_bytes and isinstance(pdf_bytes, (bytes, bytearray)) and pdf_bytes.strip().startswith(b'%PDF'):
                    msg.attach(f"ticket_{ticket.id}.pdf", pdf_bytes, 'application/pdf')
                else:
                    logger.error('Second attempt: PDF invalid for ticket %s (len=%s)', ticket.id, len(pdf_bytes) if pdf_bytes else 0)
            except Exception:
                logger.exception('Retry PDF generation failed for ticket %s', ticket.id)
    except Exception:
        logger.exception('Unexpected error validating/attaching PDF for ticket %s', ticket.id)

    try:
        msg.send(fail_silently=False)
        return True
    except Exception:
        logger.exception('Failed to send ticket email for ticket %s', ticket.id)
        return False


@csrf_exempt
def wayforpay_callback(request):
    """Handle WayForPay server callback (POST)."""
    import logging
    logger = logging.getLogger(__name__)
    merchant_signature = request.POST.get('merchantSignature') or ''
    order_reference = request.POST.get('orderReference') or ''
    if not merchant_signature or not order_reference:
        logger.warning('WayForPay callback missing signature or orderReference')
        return HttpResponse(status=400)

    if not verify_wayforpay_signature(merchant_signature, request.POST):
        logger.warning('WayForPay signature mismatch for order=%s', order_reference)
        return HttpResponse(status=400)

    ticket_id = None
    if order_reference.startswith('ticket-'):
        try:
            ticket_id = int(order_reference.split('-', 1)[1])
        except Exception:
            ticket_id = None

    ticket = None
    payment = None
    if ticket_id:
        try:
            ticket = Ticket.objects.get(pk=ticket_id)
            payment = Payment.objects.filter(ticket=ticket).order_by('-created_at').first()
        except Ticket.DoesNotExist:
            ticket = None

    status_value = (request.POST.get('reasonCode') or request.POST.get('transactionStatus') or '').strip()
    transaction_id = request.POST.get('invoiceId') or request.POST.get('transactionId') or request.POST.get('paymentId') or ''
    success = str(status_value).lower() in ('1100', 'approved', 'accept', 'success', 'successfullypaid')
    if not success:
        logger.info('WayForPay callback received non-success status=%s order=%s', status_value, order_reference)

    if ticket:
        if success:
            _apply_wayforpay_result(ticket, payment, request, dict(request.POST), success, transaction_id=transaction_id)
        else:
            _delete_failed_ticket(ticket)
        response_status = 'accept'
    else:
        response_status = 'accept' if success else 'reject'

    response_data = build_wayforpay_callback_response(order_reference, status=response_status)
    logger.info('WayForPay callback processed for order=%s status=%s', order_reference, response_status)
    return JsonResponse(response_data)


def download_ticket(request, ticket_id):
    ticket = get_object_or_404(Ticket, pk=ticket_id)
    # Allow access by owner or staff, or by valid signature provided in querystring
    sig = request.GET.get('sig') or request.GET.get('signature')
    if sig:
        if not _verify_ticket_signature(ticket, sig):
            return HttpResponse(status=403)
    else:
        allowed = False
        user = request.user
        if user.is_authenticated:
            if ticket.user_id == user.id or user.is_staff:
                allowed = True
            else:
                # allow carrier users to download tickets for their own trips
                try:
                    if getattr(getattr(user, 'profile', None), 'is_carrier', False) and getattr(ticket, 'trip', None) and getattr(ticket.trip, 'carrier_user_id', None) == user.id:
                        allowed = True
                except Exception:
                    allowed = False
        if not allowed:
            return HttpResponse(status=403)

    pdf_bytes = _generate_ticket_pdf_bytes(ticket, request)
    if not pdf_bytes:
        # Fallback: try to serve a previously generated test PDF so users still get a file
        fallback_paths = [
            os.path.join(settings.BASE_DIR, 'project', 'ticket_test.pdf'),
            os.path.join(settings.BASE_DIR, 'ticket_test.pdf'),
        ]
        for p in fallback_paths:
            try:
                if os.path.exists(p):
                    with open(p, 'rb') as f:
                        pdf_bytes = f.read()
                    break
            except Exception:
                pdf_bytes = None

    if not pdf_bytes:
        return HttpResponse('PDF generation not available', status=500)

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    inline = str(request.GET.get('inline') or request.GET.get('view') or '').lower()
    if inline in ('1', 'true', 'yes'):
        response['Content-Disposition'] = f'inline; filename=ticket_{ticket.id}.pdf'
    else:
        response['Content-Disposition'] = f'attachment; filename=ticket_{ticket.id}.pdf'
    return response


@login_required
def ticket_view(request, ticket_id):
    ticket = get_object_or_404(Ticket, pk=ticket_id)

    # allow ticket owner, staff, or carrier user assigned to the trip to view
    allowed = False
    user = request.user
    if user.is_authenticated:
        if ticket.user_id == user.id or user.is_staff:
            allowed = True
        else:
            try:
                if getattr(getattr(user, 'profile', None), 'is_carrier', False) and getattr(ticket, 'trip', None) and getattr(ticket.trip, 'carrier_user_id', None) == user.id:
                    allowed = True
            except Exception:
                allowed = False
    if not allowed:
        return HttpResponse(status=403)

    passengers = ticket.passenger_set.all()
    # last payment for display (may be None)
    try:
        last_payment = ticket.payments.order_by('-created_at').first()
    except Exception:
        last_payment = None

    # Carrier details for display on the ticket
    carrier_name = ''
    carrier_business_label = ''
    carrier_business_number = ''
    carrier_phone = ''
    try:
        if getattr(ticket, 'trip', None) and getattr(ticket.trip, 'carrier_user', None):
            carrier = getattr(ticket.trip.carrier_user, 'carrier_account', None)
            if carrier:
                carrier_name = carrier.company_name or carrier.username or ticket.trip.carrier or ''
                carrier_business_label, carrier_business_number = _get_carrier_business_details(carrier)
                carrier_phone = carrier.phone or ''
    except Exception:
        carrier_name = ticket.trip.carrier if getattr(ticket, 'trip', None) else ''

    if not carrier_name:
        carrier_name = ticket.trip.carrier if getattr(ticket, 'trip', None) else ''

    sig = _ticket_signature(ticket)

    return render(request, 'ticket_view.html', {
        'ticket': ticket,
        'passengers': passengers,
        'sig': sig,
        'last_payment': last_payment,
        'carrier_name': carrier_name,
        'carrier_business_label': carrier_business_label,
        'carrier_business_number': carrier_business_number,
        'carrier_phone': carrier_phone,
        'is_carrier_view': getattr(request.user, 'profile', None) and getattr(request.user.profile, 'is_carrier', False),
    })


def ticket_verify(request, ticket_id, signature):
    """Public verification page for drivers/staff who scan the QR code.

    Shows basic ticket data and whether signature is valid. Provides a
    link to download the PDF including the signature so drivers can open it.
    """
    ticket = get_object_or_404(Ticket, pk=ticket_id)
    sig_ok = _verify_ticket_signature(ticket, signature)
    passengers = ticket.passenger_set.all()
    download_url = request.build_absolute_uri(reverse('main:download_ticket', args=(ticket.id,))) + f"?sig={signature}"

    # If signature is valid and ticket not yet used, mark it as used (single-use)
    already_used = bool(getattr(ticket, 'used', False))
    used_at = getattr(ticket, 'used_at', None)
    valid = False
    if sig_ok and not already_used:
        try:
            ticket.used = True
            ticket.used_at = timezone.now()
            ticket.save(update_fields=['used', 'used_at'])
            valid = True
        except Exception:
            valid = False

    # If signature valid but already used, we show not-valid status with used info
    return render(request, 'ticket_verify.html', {
        'ticket': ticket,
        'passengers': passengers,
        'valid': valid,
        'signature': signature,
        'download_url': download_url,
        'already_used': already_used,
        'used_at': used_at,
    })


def cancellation_manage(request, ticket_id):
    """Public page for a ticket holder to request refund or rebooking after a trip cancellation.

    Access is protected by the ticket signature generated by `_ticket_signature`.
    The page creates an internal `SupportTicket` for staff to process the request.
    """
    from django.shortcuts import get_object_or_404
    from .models import SupportPresetQuestion, SupportTicket, SupportMessage, Ticket
    ticket = get_object_or_404(Ticket, pk=ticket_id)
    sig = request.GET.get('sig') or request.POST.get('sig')
    if not _verify_ticket_signature(ticket, sig):
        return HttpResponse(status=403)

    if request.method == 'POST':
        action = request.POST.get('action')
        message = (request.POST.get('message') or '').strip() or ''
        # normalize email target
        to_email = ticket.contact_email or (ticket.user.email if getattr(ticket, 'user', None) else None)
        # Create support request for rebooking and notify support + user by email.
        preset = SupportPresetQuestion.objects.filter(title__icontains='переброн').first()
        st = SupportTicket.objects.create(user=ticket.user, subject=f'Перебронювання квитка #{ticket.id}', ticket=ticket, preset=preset)
        SupportMessage.objects.create(ticket=st, sender=ticket.user, text=message or 'Прошу перебронювати квиток після скасування рейсу.')

        # Build list of internal support recipients: SiteConfig.contact_email and any SUPPORT_ADMINS that look like emails
        recipients = []
        try:
            from .models import SiteConfig
            sc = SiteConfig.get_solo()
            if sc and getattr(sc, 'contact_email', None):
                recipients.append(sc.contact_email)
        except Exception:
            pass

        admins = getattr(settings, 'SUPPORT_ADMINS', []) or []
        for a in admins:
            try:
                if isinstance(a, str) and '@' in a:
                    recipients.append(a)
            except Exception:
                continue

        # dedupe and filter
        try:
            recipients = list(dict.fromkeys([r for r in recipients if r]))
        except Exception:
            pass

        # send notification to support recipients
        try:
            if recipients:
                logger = logging.getLogger('main')
                subj = f"Перебронювання: квиток #{ticket.id}"
                link = request.build_absolute_uri(reverse('main:cancellation_manage', args=[ticket.id])) + f"?sig={sig}"
                body = f"Новий запит на перебронювання квитка #{ticket.id}\nКористувач: {getattr(ticket.user, 'username', '')} <{getattr(ticket.user, 'email', '')}>\nДата: {ticket.travel_date}\nСума: {ticket.total_price} {ticket.currency}\nПовідомлення: {message}\nПосилання: {link}"
                send_mail(subj, body, settings.DEFAULT_FROM_EMAIL, recipients, fail_silently=True)
        except Exception:
            logging.exception('Failed to send rebook notification emails for ticket %s', getattr(ticket, 'id', None))

        # send confirmation to ticket owner
        try:
            if to_email:
                send_mail('Ваш запит на перебронювання отримано', f'Ми отримали ваш запит на перебронювання квитка #{ticket.id}. Ваші кошти за квиток зараховані на баланс профілю, використайте їх при оформленні нового квитка.', settings.DEFAULT_FROM_EMAIL, [to_email], fail_silently=True)
        except Exception:
            logging.exception('Failed to send rebook confirmation to user for ticket %s', getattr(ticket, 'id', None))

        return render(request, 'cancellation_manage_confirm.html', {'ticket': ticket, 'action': 'rebook'})

    preselected = request.GET.get('action') or 'rebook'
    return render(request, 'cancellation_manage.html', {'ticket': ticket, 'sig': sig, 'preselected_action': preselected})


@login_required
def ticket_edit(request, ticket_id):
    # Allow staff to edit ticket fields and passenger list
    if not _support_user_allowed(request.user):
        return HttpResponse(status=403)
    ticket = get_object_or_404(Ticket, pk=ticket_id)
    if request.method == 'POST':
        form = TicketEditForm(request.POST, instance=ticket)
        formset = PassengerFormSet(request.POST, instance=ticket)
        if form.is_valid() and formset.is_valid():
            form.save()
            formset.save()
            messages.info(request, 'Квиток оновлено')
            return redirect('main:ticket_view', ticket.id)
    else:
        form = TicketEditForm(instance=ticket)
        formset = PassengerFormSet(instance=ticket)
    return render(request, 'ticket_edit.html', {'ticket': ticket, 'form': form, 'formset': formset})


@login_required
def ticket_refund(request, ticket_id):
    # Staff endpoint to perform a manual refund (credits user balance) if within 24h window
    if not _support_user_allowed(request.user):
        return JsonResponse({'error': 'forbidden'}, status=403)
    ticket = get_object_or_404(Ticket, pk=ticket_id)
    if not ticket.paid:
        return JsonResponse({'error': 'not_paid'}, status=400)
    from datetime import datetime, time, timedelta
    # Only allow refunds if the user explicitly requested it via support
    try:
        # Allow refund if user opened a support preset 'Повернення квитка' or the support ticket is linked to this ticket
        refund_requested = SupportTicket.objects.filter(user=ticket.user).filter(
            Q(ticket=ticket) | Q(preset__title__icontains='поверн') | Q(subject__icontains='поверн')
        ).exists()
    except Exception:
        refund_requested = False
    if not refund_requested:
        if request.method == 'POST':
            messages.error(request, 'Повернення не запитане клієнтом через техпідтримку')
            return redirect('main:ticket_view', ticket.id)
        return JsonResponse({'error': 'refund_not_requested'}, status=403)
    dep_dt = None
    try:
        trips = Trip.objects.filter(start_city__name__iexact=ticket.from_city, end_city__name__iexact=ticket.to_city, date=ticket.travel_date)
        if trips.exists():
            trip = trips.first()
            first_stop = trip.trip_stops.order_by('order').first()
            if first_stop and first_stop.departure_time:
                naive = datetime.combine(ticket.travel_date, first_stop.departure_time)
                tz = timezone.get_current_timezone()
                dep_dt = timezone.make_aware(naive, tz)
    except Exception:
        dep_dt = None

    if not dep_dt:
        naive = datetime.combine(ticket.travel_date, time(0, 0))
        tz = timezone.get_current_timezone()
        dep_dt = timezone.make_aware(naive, tz)

    now = timezone.localtime()
    if now > dep_dt:
        # departure already passed — no refund
        if request.method == 'POST':
            messages.error(request, 'Повернення неможливе — поїздка вже відбулась')
            return redirect('main:ticket_view', ticket.id)
        return JsonResponse({'error': 'trip_passed'}, status=400)

    time_left = dep_dt - now
    # refund rules: <24h: no refund, 24-48h: 50%, 48h+: 100%
    if time_left < timedelta(hours=24):
        if request.method == 'POST':
            messages.error(request, 'Повернення неможливе — менш ніж за 24 години до відправлення')
            return redirect('main:ticket_view', ticket.id)
        return JsonResponse({'error': 'too_late'}, status=400)
    elif time_left < timedelta(hours=48):
        refund_percent = 50
    else:
        refund_percent = 100

    # Use Decimal arithmetic to avoid mixing float and Decimal
    try:
        total_price = Decimal(ticket.total_price) if ticket.total_price is not None else Decimal('0.00')
    except Exception:
        try:
            total_price = Decimal(str(ticket.total_price))
        except Exception:
            total_price = Decimal('0.00')

    refund_amount = (total_price * Decimal(refund_percent)) / Decimal('100')

    # prepare PDF and mark ticket unpaid
    try:
        pdf_bytes = _generate_ticket_pdf_bytes(ticket)
    except Exception:
        pdf_bytes = None

    # Legacy provider refunds are removed. Always record a manual refund and credit the user balance.
    try:
        from .models import Payment
        Payment.objects.create(ticket=None, user=ticket.user, amount=-abs(refund_amount), currency=(ticket.currency or 'UAH'), status='refunded', provider='manual_refund', data={'ticket_id': ticket.id, 'by': request.user.username, 'refund_percent': refund_percent})
    except Exception:
        try:
            Payment.objects.create(ticket=ticket, user=ticket.user, amount=-abs(refund_amount), currency=(ticket.currency or 'UAH'), status='refunded', provider='manual_refund', data={'by': request.user.username, 'refund_percent': refund_percent})
        except Exception:
            pass

    try:
        profile = ticket.user.profile
        profile.balance = (profile.balance or Decimal('0.00')) + refund_amount
        profile.save(update_fields=['balance'])
    except Exception:
        logging.exception('Failed to credit user balance')

    # mark unpaid (or partially refunded)
    try:
        ticket.paid = False
        ticket.save(update_fields=['paid'])
    except Exception:
        pass

    # Send notification email to user
    try:
        subject = f"Повернення коштів за квиток #{ticket.id}"
        body = f"Ваш квиток #{ticket.id} було оброблено для повернення. Сума {refund_amount} {ticket.currency or 'UAH'} зарахована на ваш баланс в системі ({refund_percent}% від суми). Квиток більше недійсний."
        msg = EmailMessage(subject, body, settings.DEFAULT_FROM_EMAIL, [ticket.user.email])
        if pdf_bytes and isinstance(pdf_bytes, (bytes, bytearray)) and pdf_bytes.strip().startswith(b'%PDF'):
            try:
                msg.attach(f"ticket_{ticket.id}_REFUNDED.pdf", pdf_bytes, 'application/pdf')
            except Exception:
                pass
        msg.send(fail_silently=True)
    except Exception:
        logging.exception('Failed to send refund email')

    try:
        user_id = ticket.user_id
        ticket.delete()
    except Exception:
        user_id = ticket.user_id if hasattr(ticket, 'user_id') else None

    if request.method == 'POST':
        messages.info(request, f'Квиток оброблено. На баланс користувача зараховано {refund_amount} {ticket.currency or "UAH"} ({refund_percent}%).')
        if user_id:
            return redirect('main:support_admin_user', user_id)
        return redirect('main:profile')
    return JsonResponse({'ok': True, 'refund_amount': float(refund_amount), 'refund_percent': refund_percent})

class BusListView(ListView):
    model = Bus
    template_name = "nashbusindex.html"

    def get_queryset(self):
        return Bus.objects.filter(is_published=True)


class BusDetailView(DetailView):
    model = Bus
    template_name = "bus_detail.html"
    context_object_name = "bus"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = BusBookingForm()
        return context


def bus_booking(request, slug):
    bus = get_object_or_404(Bus, slug=slug)

    if request.method == "POST":
        form = BusBookingForm(request.POST)
        if form.is_valid():
            booking = form.save(commit=False)
            booking.bus = bus
            booking.save()
            return redirect("main:bus_detail", slug=slug)

    return redirect("main:bus_detail", slug=slug)

def bus_detail(request, slug):
    bus = get_object_or_404(Bus, slug=slug)
    return render(request, 'bus_detail.html', {'bus': bus})


def contacts(request):
    if request.method == "POST":
        name = request.POST.get("name")
        phone = request.POST.get("phone")
        message = request.POST.get("message")

        print(name, phone, message)  # поки просто лог

    return render(request, "contacts.html")