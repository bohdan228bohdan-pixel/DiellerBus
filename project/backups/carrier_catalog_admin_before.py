from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.contrib import messages
from django.utils import timezone
from django.conf import settings
import logging
from .models import Profile, City, Route, RouteStop, Trip, TripStop, TripDayAvailability

class RouteStopInline(admin.TabularInline):
    model = RouteStop
    extra = 1
    fields = ('order', 'city', 'address')
    autocomplete_fields = ('city',)


@admin.register(Route)
class RouteAdmin(admin.ModelAdmin):
    list_display = ('name', 'active', 'include_subcities')
    search_fields = ('^name',)
    ordering = ('name',)
    inlines = [RouteStopInline]


class TripStopInline(admin.TabularInline):
    model = TripStop
    extra = 1
    fields = ('order', 'city', 'arrival_time', 'departure_time', 'price', 'address')
    autocomplete_fields = ('city',)


class TripDayAvailabilityInline(admin.TabularInline):
    """Inline to edit per-day availability for a Trip (month-ahead grid handled via rows)."""
    model = TripDayAvailability
    extra = 0
    fields = ('date', 'available')
    ordering = ('date',)


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = ('title', 'carrier_user_display', 'route', 'date', 'direction', 'currency', 'discount_percent', 'seats', 'active', 'manage_fares_link')
    list_filter = ('date', 'direction', 'active', 'carrier_user')
    # use starts-with searches for faster prefix matching in admin search box
    search_fields = ('^title', '^route__name', '^carrier', '^carrier_user__username')
    inlines = [TripStopInline, TripDayAvailabilityInline]
    # hide base_price from the Trip form; fares should be edited in Manage Fares
    exclude = ('base_price',)
    autocomplete_fields = ('route', 'start_city', 'end_city', 'carrier_user')

    ordering = ('carrier', 'route__name', 'date')
    actions = ['mark_as_cancelled']

    def get_changeform_initial_data(self, request):
        """Allow pre-filling `carrier_user` when adding a Trip via a carrier 'folder' link."""
        initial = super().get_changeform_initial_data(request)
        cu = request.GET.get('carrier_user')
        try:
            if cu:
                initial['carrier_user'] = int(cu)
        except Exception:
            pass
        return initial

    def carrier_user_display(self, obj):
        if getattr(obj, 'carrier_user', None):
            return obj.carrier_user.username
        return obj.carrier or ''
    carrier_user_display.short_description = 'Перевізник'

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        try:
            if request.user.is_staff and getattr(getattr(request.user, 'profile', None), 'is_carrier', False):
                return qs.filter(carrier_user=request.user)
        except Exception:
            pass
        return qs

    def has_change_permission(self, request, obj=None):
        # allow carrier users (staff) to change only their own trips
        if request.user.is_staff and getattr(getattr(request.user, 'profile', None), 'is_carrier', False):
            if obj is None:
                return True
            return getattr(obj, 'carrier_user_id', None) == request.user.id
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if request.user.is_staff and getattr(getattr(request.user, 'profile', None), 'is_carrier', False):
            if obj is None:
                return True
            return getattr(obj, 'carrier_user_id', None) == request.user.id
        return super().has_delete_permission(request, obj)

    def get_urls(self):
        from django.urls import path
        urls = super().get_urls()
        custom = [
            path('<int:trip_id>/manage-fares/', self.admin_site.admin_view(self.manage_fares_view), name='main_trip_manage_fares'),
        ]
        return custom + urls

    def manage_fares_link(self, obj):
        try:
            url = reverse('admin:main_trip_manage_fares', args=(obj.id,))
            return format_html('<a class="button" href="{}">Управління тарифами</a>', url)
        except Exception:
            return '-'
    manage_fares_link.short_description = 'Тарифи'

    def manage_fares_view(self, request, trip_id):
        from django.shortcuts import get_object_or_404, render, redirect
        from django.contrib import messages
        from decimal import Decimal, InvalidOperation

        trip = get_object_or_404(Trip, pk=trip_id)
        if not self.has_change_permission(request, obj=trip):
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden('Недостатньо прав')

        # collect stops' cities
        stops = list(trip.trip_stops.select_related('city').order_by('order'))
        cities = [s.city for s in stops if getattr(s, 'city', None)]

        # split by country code heuristically
        def is_ua_city(c):
            if not c: return False
            cc = (c.country or '').strip().lower()
            return cc.startswith('ua') or 'ukr' in cc
        def is_pl_city(c):
            if not c: return False
            cc = (c.country or '').strip().lower()
            return cc.startswith('pl') or 'pol' in cc

        ua_cities = [c for c in cities if is_ua_city(c)]
        pl_cities = [c for c in cities if is_pl_city(c)]

        # Choose rows/cols based on trip direction and international flag
        if trip.direction == 'UA_PL':
            rows = ua_cities
            cols = pl_cities
        elif trip.direction == 'PL_UA':
            rows = pl_cities
            cols = ua_cities
        else:
            # fallback: use start city as row and remaining as cols
            rows = [trip.start_city] if trip.start_city else (cities[:1] if cities else [])
            cols = [c for c in cities if c not in rows]

        # existing fares map
        fares_qs = trip.fares.select_related('from_city', 'to_city').all()
        fares_map = {f"{f.from_city_id}_{f.to_city_id}": f for f in fares_qs}

        if request.method == 'POST':
            # parse submitted prices
            updated = 0
            for key, val in request.POST.items():
                if not key.startswith('price_'):
                    continue
                try:
                    _, from_id, to_id = key.split('_')
                    from_id = int(from_id); to_id = int(to_id)
                except Exception:
                    continue
                price_str = (val or '').strip()
                existing = fares_map.get(f"{from_id}_{to_id}")
                if price_str == '':
                    if existing:
                        existing.delete(); updated += 1
                    continue
                try:
                    price_val = Decimal(price_str.replace(',', '.'))
                except InvalidOperation:
                    continue
                if existing:
                    if existing.price != price_val:
                        existing.price = price_val
                        existing.currency = trip.currency
                        existing.save()
                        updated += 1
                else:
                    TripFare.objects.create(trip=trip, from_city_id=from_id, to_city_id=to_id, price=price_val, currency=trip.currency)
                    updated += 1
            messages.success(request, f'Збережено {updated} тарифів')
            return redirect(reverse('admin:main_trip_manage_fares', args=(trip.id,)))

        # build matrix rows for template: list of {from: city, cells: [{to, price, key}]}
        matrix = []
        for r in rows:
            cells = []
            for c in cols:
                key = f"{r.id}_{c.id}"
                fobj = fares_map.get(key)
                price_val = (fobj.price if fobj is not None else '')
                cells.append({'to': c, 'price': price_val, 'key': key, 'from_id': r.id, 'to_id': c.id})
            matrix.append({'from_city': r, 'cells': cells})

        context = dict(
            self.admin_site.each_context(request),
            trip=trip,
            matrix=matrix,
            rows=rows,
            cols=cols,
        )
        return render(request, 'admin/main/trip_manage_fares.html', context)

    def mark_as_cancelled(self, request, queryset):
        """Admin action: mark selected Trip(s) as cancelled and notify ticket holders.

        For each Trip in queryset, set `active=False` and email all paid ticket holders
        a secure link where they can request refund or rebooking.
        """
        from django.core.mail import send_mail
        from django.urls import reverse
        from .models import Ticket
        notified = 0
        for trip in queryset:
            try:
                trip.active = False
                trip.save(update_fields=['active'])
            except Exception:
                continue
            tickets_qs = Ticket.objects.filter(trip=trip, travel_date=trip.date, paid=True)
            for ticket in tickets_qs:
                try:
                    # generate secure signature for ticket link
                    from .views import _ticket_signature
                    sig = _ticket_signature(ticket)
                except Exception:
                    sig = ''
                try:
                    link = request.build_absolute_uri(reverse('main:cancellation_manage', args=(ticket.id,))) + f'?sig={sig}'
                    to_email = ticket.contact_email or (ticket.user.email if getattr(ticket, 'user', None) else None)
                    subject = f'Рейс скасовано — опції для квитка #{ticket.id}'
                    body = (
                        f'Ваш рейс {trip} ({trip.date}) було скасовано.\n\n'
                        f'Щоб вибрати опцію (повернення коштів або перебронювання), перейдіть за посиланням:\n{link}\n\n'
                        'Якщо потрібно, зверніться до нашої служби підтримки.'
                    )
                    if to_email:
                        try:
                            from .email_utils import send_email
                            send_email(subject, body, [to_email], from_email=settings.DEFAULT_FROM_EMAIL, fail_silently=False, async_send=True)
                            notified += 1
                        except Exception:
                            logging.exception('Failed to send cancellation email for ticket %s', ticket.id)
                except Exception:
                    continue
        self.message_user(request, f"Скасовано {queryset.count()} рейсів; повідомлено {notified} квитків.")
    mark_as_cancelled.short_description = 'Позначити рейс(и) як скасовані і повідомити клієнтів'


@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    # show parent (main city) and allow editing subcities inline
    list_display = ('name', 'country', 'parent')
    search_fields = ('^name',)
    ordering = ('name',)
    class SubcityInline(admin.TabularInline):
        model = City
        fk_name = 'parent'
        extra = 0
        fields = ('name', 'country')
        autocomplete_fields = ()
    inlines = [SubcityInline]


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'avatar', 'balance', 'phone', 'is_carrier', 'created_at']
    search_fields = ['^user__username', '^user__email', '^phone']
    list_filter = ['created_at', 'is_carrier']
    readonly_fields = ('created_at', 'updated_at', 'user_link', 'tickets_list')
    readonly_fields = ('created_at', 'updated_at', 'user_link', 'tickets_list', 'user_details')
    fields = ('user', 'is_carrier', 'user_link', 'user_details', 'avatar', 'phone', 'balance', 'tickets_list')

    def user_link(self, obj):
        if not obj or not obj.user:
            return '-'
        url = reverse('admin:auth_user_change', args=(obj.user.id,))
        return format_html('<a href="{}">{}</a>', url, obj.user.username)
    user_link.short_description = 'Акаунт користувача'

    def tickets_list(self, obj):
        if not obj or not obj.user:
            return 'Немає квитків'
        from .models import Ticket
        qs = Ticket.objects.filter(user=obj.user).order_by('-created_at')[:50]
        if not qs.exists():
            return 'Немає квитків'
        return format_html_join('\n', '<div><a href="{}">Квиток #{} — {} → {} — {}</a></div>', (
            (reverse('admin:main_ticket_change', args=(t.id,)), t.id, (t.from_city or t.route or ''), (t.to_city or ''), ('Оплачено' if t.paid else 'Не оплачено')) for t in qs
        ))
    tickets_list.short_description = 'Квитки (останні 50)'

    def user_details(self, obj):
        if not obj or not obj.user:
            return '-'
        u = obj.user
        # For security, do not display raw password hashes in the admin UI.
        date_joined = u.date_joined.strftime('%d.%m.%Y %H:%M') if getattr(u, 'date_joined', None) else '-'
        last_login = u.last_login.strftime('%d.%m.%Y %H:%M') if getattr(u, 'last_login', None) else '-'
        return format_html(
            '<div>Логін: <strong>{}</strong><br>Email: {}<br>Пароль: <code style="color:#ddd">(hidden)</code><br>Зареєстрований: {}<br>Останній вхід: {}</div>',
            u.username, u.email or '-', date_joined, last_login
        )
    user_details.short_description = 'Дані користувача'

from .models import Bus, BusBooking, Ticket, Carrier, TripFare, FareFolder
from .models import SupportPresetQuestion, SupportTicket, SupportMessage
from .models import SupportWorker
from .models import Passenger, Payment
from .models import SiteConfig, StaticPage

@admin.register(Bus)
class BusAdmin(admin.ModelAdmin):
    list_display = ("title", "seats", "price_per_hour", "is_published")
    prepopulated_fields = {"slug": ("title",)}
    ordering = ('title',)


@admin.register(BusBooking)
class BusBookingAdmin(admin.ModelAdmin):
    list_display = ("bus", "customer_name", "status", "created_at")


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'from_city', 'to_city', 'travel_date', 'total_price', 'currency', 'discount_percent', 'paid', 'created_at', 'view_link')
    list_filter = ('paid', 'travel_date')
    search_fields = ('^user__username', '^from_city', '^to_city', '^route')
    inlines = []

    class PassengerInline(admin.TabularInline):
        model = Passenger
        extra = 0
        fields = ('first_name', 'last_name')

    class PaymentInline(admin.TabularInline):
        model = Payment
        extra = 0
        readonly_fields = ('provider', 'provider_payment_id', 'amount', 'currency', 'status', 'created_at')

    inlines = [PassengerInline, PaymentInline]
    actions = ['mark_as_paid', 'mark_as_unpaid']

    def mark_as_paid(self, request, queryset):
        from .models import Payment
        from django.core.mail import EmailMessage
        count = 0
        for ticket in queryset:
            if not ticket.paid:
                Payment.objects.create(ticket=ticket, user=ticket.user, provider='manual', provider_payment_id='manual', amount=ticket.total_price, currency=(ticket.currency or 'UAH'), status='success', data={'admin_marked_by': request.user.username})
                ticket.paid = True
                ticket.save(update_fields=['paid'])
                # try to send ticket email
                try:
                    from .views import _send_ticket_email
                    _send_ticket_email(ticket, Payment.objects.filter(ticket=ticket).order_by('-created_at').first())
                except Exception:
                    pass
                count += 1
        self.message_user(request, f"Позначено як оплачені {count} квитків.")
    mark_as_paid.short_description = 'Позначити вибрані квитки як оплачені'

    def mark_as_unpaid(self, request, queryset):
        from .models import Payment
        count = 0
        for ticket in queryset:
            if ticket.paid:
                try:
                    Payment.objects.create(ticket=ticket, user=ticket.user, provider='manual', provider_payment_id='manual_refund', amount=-abs(ticket.total_price), currency=(ticket.currency or 'UAH'), status='refunded', data={'admin_unpaid_by': request.user.username})
                except Exception:
                    pass
                ticket.paid = False
                ticket.save(update_fields=['paid'])
                count += 1
        self.message_user(request, f"Позначено як неоплачені {count} квитків.")
    mark_as_unpaid.short_description = 'Позначити вибрані квитки як неоплачені'

    def view_link(self, obj):
        try:
            url = reverse('main:ticket_view', args=(obj.id,))
            return format_html('<a href="{}" target="_blank">Переглянути</a>', url)
        except Exception:
            return '-'
    view_link.short_description = 'Перегляд'


@admin.register(SupportPresetQuestion)
class SupportPresetQuestionAdmin(admin.ModelAdmin):
    list_display = ('title', 'order')


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'subject', 'preset', 'status', 'assigned_to', 'last_message_at')
    list_filter = ('status',)
    search_fields = ('^user__username', '^subject')


@admin.register(SupportMessage)
class SupportMessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'ticket', 'sender', 'created_at')


@admin.register(SupportWorker)
class SupportWorkerAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'email', 'user', 'created_at')
    search_fields = ('^username', '^email', '^full_name', '^user__username')
    readonly_fields = ('user', 'created_at')
    fields = ('full_name', 'username', 'email', 'avatar', 'user', 'created_at')
    
    def save_model(self, request, obj, form, change):
        # capture whether user existed before saving
        existed = bool(obj.user_id)
        super().save_model(request, obj, form, change)
        # if a new user was created by the model, and model stored a raw password, show it once to admin
        try:
            if not existed and getattr(obj, '_raw_password', None):
                # Do not emit raw passwords into admin messages (security risk).
                obj._raw_password = None
        except Exception:
            pass


@admin.register(Carrier)
class CarrierAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'company_name', 'business_type', 'business_number', 'phone', 'email', 'user', 'created_at')
    search_fields = ('^company_name', '^username', '^email', '^user__username', '^business_number', '^phone')
    readonly_fields = ('user', 'created_at')
    fields = ('company_name', 'business_type', 'business_number', 'phone', 'username', 'email', 'avatar', 'user', 'created_at', 'related_links')
    ordering = ('company_name',)
    readonly_fields = readonly_fields + ('related_links',)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.base_fields['company_name'].required = True
        form.base_fields['business_type'].required = True
        form.base_fields['business_number'].required = True
        form.base_fields['phone'].required = True
        return form

    def related_links(self, obj):
        """Render quick links to Trips / Add trip / Fares filtered to this carrier."""
        if not obj or not getattr(obj, 'user_id', None):
            return '-'
        try:
            trips_url = reverse('admin:main_trip_changelist') + f'?carrier_user__id__exact={obj.user_id}'
            add_trip_url = reverse('admin:main_trip_add') + f'?carrier_user={obj.user_id}'
            fares_url = reverse('admin:main_tripfare_changelist') + f'?trip__carrier_user__id__exact={obj.user_id}'
            # count trips for display
            trips_count = Trip.objects.filter(carrier_user_id=obj.user_id).count()
            return format_html(
                '<div style="display:flex;gap:8px;flex-wrap:wrap">'
                '<a class="button" href="{}">Рейси ({})</a>'
                '<a class="button" href="{}">Додати рейс</a>'
                '<a class="button" href="{}">Тарифи</a>'
                '</div>',
                trips_url, trips_count, add_trip_url, fares_url
            )
        except Exception:
            return '-'
    related_links.short_description = 'Панель перевізника'

    def save_model(self, request, obj, form, change):
        existed = bool(obj.user_id)
        super().save_model(request, obj, form, change)
        try:
            if not existed and getattr(obj, '_raw_password', None):
                # Avoid showing raw credentials in admin messages.
                obj._raw_password = None
        except Exception:
            pass


@admin.register(SiteConfig)
class SiteConfigAdmin(admin.ModelAdmin):
    list_display = ('shop_name', 'contact_email', 'contact_phone')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(StaticPage)
class StaticPageAdmin(admin.ModelAdmin):
    list_display = ('slug', 'title', 'is_published', 'language', 'order')
    list_filter = ('is_published', 'language')
    search_fields = ('^slug', '^title')
    fields = ('slug', 'title', 'content', 'is_published', 'language', 'order')
    ordering = ('title',)


@admin.register(TripFare)
class TripFareAdmin(admin.ModelAdmin):
    list_display = ('trip', 'from_city', 'to_city', 'price', 'currency')
    # starts-with searches for faster prefix matching and autocompletes
    search_fields = ('^trip__route__name', '^from_city__name', '^to_city__name')
    autocomplete_fields = ('trip', 'from_city', 'to_city')
    list_filter = ('currency',)
    ordering = ('trip__route__name', 'from_city__name', 'to_city__name')

    list_display = ('trip', 'from_city', 'to_city', 'price', 'currency', 'folder')
    list_filter = ('currency', 'folder')
    search_fields = ('^trip__route__name', '^from_city__name', '^to_city__name', '^folder__name')
    actions = ['create_folder_from_selected']

    def create_folder_from_selected(self, request, queryset):
        """Admin action: create a FareFolder and assign selected TripFare rows to it.

        If selected fares belong to multiple carriers, abort and ask user to select fares
        for a single carrier only.
        """
        if not queryset.exists():
            self.message_user(request, 'Нічого не вибрано.', level=messages.WARNING)
            return
        # collect carrier_user ids from fares' trips
        carrier_user_ids = set()
        for f in queryset.select_related('trip__carrier_user'):
            carrier_user_ids.add(getattr(getattr(f.trip, 'carrier_user', None), 'id', None))
        carrier_user_ids.discard(None)
        if len(carrier_user_ids) > 1:
            self.message_user(request, 'Вибрані тарифи належать різним перевізникам. Виберіть тарифи тільки одного перевізника.', level=messages.ERROR)
            return
        carrier = None
        if carrier_user_ids:
            cu_id = next(iter(carrier_user_ids))
            carrier = Carrier.objects.filter(user_id=cu_id).first()

        name = f"Папка тарифів — {carrier.company_name if carrier else 'Без перевізника'} — {timezone.now().strftime('%Y-%m-%d %H:%M')}"
        folder = FareFolder.objects.create(name=name, carrier=carrier)
        updated = queryset.update(folder=folder)
        self.message_user(request, f'Створено папку "{folder.name}" і призначено {updated} тарифів.', level=messages.SUCCESS)
    create_folder_from_selected.short_description = 'Створити папку з виділених тарифів'


@admin.register(FareFolder)
class FareFolderAdmin(admin.ModelAdmin):
    list_display = ('name', 'carrier', 'fares_count', 'created_at')
    search_fields = ('^name', '^carrier__company_name')
    readonly_fields = ('created_at',)

    def fares_count(self, obj):
        try:
            return obj.fares.count()
        except Exception:
            return 0
    fares_count.short_description = 'Тарифи'