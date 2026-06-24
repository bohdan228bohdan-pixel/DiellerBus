from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.urls import reverse
from django.utils.text import slugify
import uuid
from django.utils import timezone
from django.conf import settings

# Create your models here.

class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    phone = models.CharField(max_length=20, blank=True, null=True)
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    # mark whether this profile belongs to a carrier (перевізник)
    is_carrier = models.BooleanField(default=False, verbose_name='Профіль перевізника')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username}'s Profile"

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance)

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    if hasattr(instance, 'profile'):
        instance.profile.save()


@receiver(post_delete, sender=Profile)
def delete_user_when_profile_deleted(sender, instance, **kwargs):
    """When a Profile is deleted from admin, also delete the linked User if it still exists.

    This prevents orphaned users remaining after an admin removes a Profile. We check
    existence to avoid interfering with cascade delete when the User itself was removed.
    """
    try:
        user_id = getattr(instance, 'user_id', None)
        if user_id and User.objects.filter(pk=user_id).exists():
            User.objects.filter(pk=user_id).delete()
    except Exception:
        pass

class EmailVerification(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    code = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} - {self.code}"


class PasswordChangeRequest(models.Model):
    """Temporary password-change request. Stores a verification code and the
    hashed new password until the user confirms the code via email.

    Storing the hashed password (as produced by `make_password`) is safe and
    avoids keeping raw passwords in plaintext.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='password_change_requests')
    code = models.CharField(max_length=6)
    password_hash = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    used = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'Запит на зміну пароля'
        verbose_name_plural = 'Запити на зміну пароля'

    def __str__(self):
        return f"PasswordChangeRequest for {self.user.username} — {'used' if self.used else 'pending'}"

    def is_valid(self):
        if self.used:
            return False
        if self.expires_at and timezone.now() > self.expires_at:
            return False
        return True



class Ticket(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    # optional link to a Trip (if the ticket was bought for a scheduled Trip)
    trip = models.ForeignKey('Trip', on_delete=models.SET_NULL, null=True, blank=True, related_name='tickets')
    # legacy/free-text route field
    route = models.CharField(max_length=255, default="", blank=True)

    # explicit ticket fields (used by views)
    from_city = models.CharField(max_length=200, blank=True, null=True)
    to_city = models.CharField(max_length=200, blank=True, null=True)
    travel_date = models.DateField(blank=True, null=True)
    passengers = models.IntegerField(default=1)
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    # store currency and applied discount percent on the ticket for historical accuracy
    currency = models.CharField(max_length=10, default='UAH')
    discount_percent = models.PositiveIntegerField(default=0)
    # contact info captured at purchase time
    contact_email = models.EmailField(blank=True, null=True)
    contact_phone = models.CharField(max_length=32, blank=True, null=True)
    paid = models.BooleanField(default=False)
    stripe_session_id = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # mark whether the ticket was already used (scanned/validated)
    used = models.BooleanField(default=False)
    used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        user = getattr(self.user, 'username', 'unknown')
        if self.from_city and self.to_city:
            return f"{user} | {self.from_city} -> {self.to_city} | {self.total_price} грн"
        return f"{user} | {self.route} | {self.total_price} грн"


class Payment(models.Model):
    PAYMENT_STATUS = (
        ("pending", "Очікує"),
        ("success", "Успішно"),
        ("failure", "Неуспішно"),
        ("refunded", "Повернуто"),
    )

    ticket = models.ForeignKey('Ticket', on_delete=models.CASCADE, related_name='payments', null=True, blank=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    provider = models.CharField(max_length=50, default='liqpay')
    provider_payment_id = models.CharField(max_length=255, blank=True, null=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    currency = models.CharField(max_length=10, default='UAH')
    status = models.CharField(max_length=20, choices=PAYMENT_STATUS, default='pending')
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Payment {self.id} — {self.provider} — {self.status} — {self.amount} {self.currency}"

    def save(self, *args, **kwargs):
        """Scrub known sensitive keys from the `data` JSON before saving.

        Providers sometimes return payloads that include card PAN/CVV or other
        sensitive fragments. We proactively remove likely keys so raw card data
        is not persisted in the database.
        """
        try:
            if isinstance(self.data, dict):
                # keys that commonly contain PAN/CVV/token info — case-insensitive
                sensitive_indicators = ('card', 'pan', 'cvv', 'cvc', 'expiry', 'expiration', 'token')
                for key in list(self.data.keys()):
                    lk = key.lower()
                    if any(ind in lk for ind in sensitive_indicators):
                        # remove suspicious field entirely
                        self.data.pop(key, None)
        except Exception:
            # be conservative: if scrubbing fails, don't prevent saving
            pass
        super().save(*args, **kwargs)


class Passenger(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='passenger_set')
    first_name = models.CharField(max_length=120)
    last_name = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.first_name} {self.last_name or ''}".strip()


# --- New models for routes, cities, trips ---
class City(models.Model):
    name = models.CharField(max_length=200)
    country = models.CharField(max_length=100, blank=True)
    # optional link to a main/parent city (subcity relationship)
    parent = models.ForeignKey('self', null=True, blank=True, related_name='subcities', on_delete=models.SET_NULL, verbose_name='Головне місто')

    class Meta:
        verbose_name = "Місто"
        verbose_name_plural = "Міста"

    def __str__(self):
        return self.name


class Route(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    # when True, fares defined for a main city will be applied for its subcities
    include_subcities = models.BooleanField(default=False, verbose_name='Включати підміста', help_text='Якщо відмічено, підміста рахуються по тарифу головного міста')

    class Meta:
        verbose_name = "Маршрут"
        verbose_name_plural = "Маршрути"

    def __str__(self):
        return self.name


class RouteStop(models.Model):
    route = models.ForeignKey(Route, on_delete=models.CASCADE, related_name='stops')
    city = models.ForeignKey(City, on_delete=models.CASCADE)
    order = models.PositiveIntegerField(default=0)
    address = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['order']
        verbose_name = "Зупинка"
        verbose_name_plural = "Зупинки"

    def __str__(self):
        return f"{self.route.name} — {self.city.name} ({self.order})"


class Trip(models.Model):
    DIRECTION_CHOICES = [
        ('UA_PL', 'Україна → Польща'),
        ('PL_UA', 'Польща → Україна'),
        ('UA_UA', 'Україна → Україна'),
    ]

    route = models.ForeignKey(Route, on_delete=models.CASCADE, related_name='trips')
    title = models.CharField(max_length=255, blank=True)
    # human-readable carrier name (legacy) and optional linked carrier user account
    carrier = models.CharField(max_length=255, blank=True, default='')
    carrier_user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='carrier_trips')
    date = models.DateField(blank=True, null=True)
    seats = models.PositiveIntegerField(default=50)
    base_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    currency = models.CharField(max_length=10, default='UAH')
    start_city = models.ForeignKey('City', on_delete=models.SET_NULL, null=True, blank=True, related_name='starting_trips')
    end_city = models.ForeignKey('City', on_delete=models.SET_NULL, null=True, blank=True, related_name='ending_trips')
    discount_percent = models.PositiveIntegerField(default=0)
    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES, default='UA_PL')
    # whether this trip is international (abroad) — editable in admin
    is_international = models.BooleanField(default=False, verbose_name='Рейс за кордон')
    active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Рейс"
        verbose_name_plural = "Рейси"

    def __str__(self):
        return f"{self.route.name} — {self.title or self.date or self.id}"

    def is_available_on(self, date_obj):
        """Return True if the trip is available on the given date.

        If no explicit availability record exists for the date, default to True.
        """
        try:
            if not date_obj:
                return True
            rec = self.day_availabilities.filter(date=date_obj).first()
            if rec is None:
                return True
            return bool(rec.available)
        except Exception:
            return True


class TripStop(models.Model):
    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name='trip_stops')
    city = models.ForeignKey(City, on_delete=models.CASCADE)
    order = models.PositiveIntegerField(default=0)
    arrival_time = models.TimeField(blank=True, null=True)
    departure_time = models.TimeField(blank=True, null=True)
    price = models.DecimalField(max_digits=8, decimal_places=2, default=0.00)
    address = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['order']
        verbose_name = "Зупинка рейсу"
        verbose_name_plural = "Зупинки рейсу"

    def __str__(self):
        return f"{self.trip} — {self.city.name} ({self.order})"


class TripFare(models.Model):
    """Optional explicit fares for a Trip between two cities.

    This allows admins to set a direct price for a pair (from_city -> to_city)
    without editing individual leg prices. When present, `api_trips` will use
    these fares for price display and checkout.
    """
    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name='fares')
    from_city = models.ForeignKey(City, on_delete=models.CASCADE, related_name='+')
    to_city = models.ForeignKey(City, on_delete=models.CASCADE, related_name='+')
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    currency = models.CharField(max_length=10, blank=True, null=True)
    # Optional grouping of fares into admin "folders" for easier organization per carrier
    folder = models.ForeignKey('FareFolder', null=True, blank=True, on_delete=models.SET_NULL, related_name='fares')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (('trip', 'from_city', 'to_city'),)
        verbose_name = 'Тариф (пара міст)'
        verbose_name_plural = 'Тарифи (пари міст)'

    def __str__(self):
        cur = f" {self.currency}" if self.currency else ''
        return f"{self.trip} — {self.from_city.name} → {self.to_city.name}: {self.price}{cur}"


class FareFolder(models.Model):
    """Administrative grouping for fares — a simple 'folder' tied to a Carrier.

    Admins can create folders and then assign `TripFare.folder` to group fares
    for easier navigation and filtering in admin list views.
    """
    name = models.CharField(max_length=255)
    carrier = models.ForeignKey('Carrier', null=True, blank=True, on_delete=models.CASCADE, related_name='fare_folders')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Папка тарифів'
        verbose_name_plural = 'Папки тарифів'
        ordering = ('name',)

    def __str__(self):
        return self.name


# Keep Trip.start_city / end_city synced with the first/last TripStop
@receiver(post_save, sender=TripStop)
def update_trip_bounds_on_save(sender, instance, **kwargs):
    try:
        trip = instance.trip
        stops = list(trip.trip_stops.order_by('order').select_related('city'))
        if stops:
            start = stops[0].city
            end = stops[-1].city
            changed = False
            if trip.start_city_id != (start.id if start else None):
                trip.start_city = start
                changed = True
            if trip.end_city_id != (end.id if end else None):
                trip.end_city = end
                changed = True
            if changed:
                trip.save(update_fields=['start_city', 'end_city'])
    except Exception:
        pass


@receiver(post_delete, sender=TripStop)
def update_trip_bounds_on_delete(sender, instance, **kwargs):
    try:
        trip = instance.trip
        stops = list(trip.trip_stops.order_by('order').select_related('city'))
        if stops:
            start = stops[0].city
            end = stops[-1].city
        else:
            start = None
            end = None
        changed = False
        if trip.start_city_id != (start.id if start else None):
            trip.start_city = start
            changed = True
        if trip.end_city_id != (end.id if end else None):
            trip.end_city = end
            changed = True
        if changed:
            trip.save(update_fields=['start_city', 'end_city'])
    except Exception:
        pass


class TripDayAvailability(models.Model):
    """Per-day availability record for a Trip.

    Admin may set `available=False` for a given date to stop selling tickets
    for that trip on that day.
    """
    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name='day_availabilities')
    date = models.DateField()
    available = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = (('trip', 'date'),)
        ordering = ('date',)
        verbose_name = 'Доступність рейсу за датою'
        verbose_name_plural = 'Доступність рейсу за датою'

    def __str__(self):
        return f"{self.trip} — {self.date.isoformat()} — {'Їде' if self.available else 'Не їде'}"


class Bus(models.Model):
    title = models.CharField("Назва/марка", max_length=200)
    slug = models.SlugField("Слаг", max_length=220, unique=True, blank=True)
    def _bus_image_upload_to(instance, filename):
        # generate a safe, mostly-unique filename for uploaded bus images
        base, dot, ext = filename.rpartition('.')
        ext = ext or 'jpg'
        safe = (instance.slug or slugify(getattr(instance, 'title', 'bus')) or 'bus')
        unique = uuid.uuid4().hex[:8]
        return f"buses/{safe}_{unique}.{ext}"

    image = models.ImageField("Фото автобуса", upload_to=_bus_image_upload_to, blank=True, null=True)
    seats = models.PositiveIntegerField("Кількість місць", default=50)
    contact_name = models.CharField("Контактна особа", max_length=120, blank=True)
    contact_phone = models.CharField("Телефон для замовлення", max_length=32, help_text="Формат: +380XXXXXXXXX або локальний")
    price_per_hour = models.DecimalField("Ціна за годину (UAH)", max_digits=9, decimal_places=2, null=True, blank=True)
    price_note = models.CharField("Примітка про ціну", max_length=255, default="Ціна за км уточнюється у водія")
    description = models.TextField("Опис", blank=True)
    is_published = models.BooleanField("Опубліковано", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Автобус"
        verbose_name_plural = "Автобуси"

    def __str__(self):
        return f"{self.title} — {self.seats} місць"

    def get_absolute_url(self):
      return reverse("main:bus_detail", args=[self.slug])

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title)[:180]
            slug = base
            n = 1
            while Bus.objects.filter(slug=slug).exists():
                slug = f"{base}-{n}"
                n += 1
            self.slug = slug
        super().save(*args, **kwargs)


class BusBooking(models.Model):
    STATUS_CHOICES = (
        ("pending", "Очікує підтвердження"),
        ("confirmed", "Підтверджено"),
        ("cancelled", "Скасовано"),
    )
    bus = models.ForeignKey(Bus, on_delete=models.CASCADE, related_name="bookings")
    customer_name = models.CharField("Ім'я замовника", max_length=150)
    customer_phone = models.CharField("Телефон замовника", max_length=32)
    date_from = models.DateField("Дата початку", null=True, blank=True)
    time_from = models.TimeField("Час початку", null=True, blank=True)
    hours = models.PositiveIntegerField("Кількість годин", default=1)
    estimated_km = models.PositiveIntegerField("Орієнтовні км", null=True, blank=True)
    price_estimate = models.DecimalField("Орієнтовна ціна", max_digits=10, decimal_places=2, null=True, blank=True)
    status = models.CharField("Статус", max_length=20, choices=STATUS_CHOICES, default="pending")
    note = models.TextField("Коментар/побажання", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Бронювання автобуса"
        verbose_name_plural = "Бронювання автобусів"

    def __str__(self):
        return f"Бронь: {self.bus} — {self.customer_name} ({self.customer_phone})"
    

# === Технічна підтримка ===
class SupportPresetQuestion(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order']
        verbose_name = "Тип запиту техпідтримки"
        verbose_name_plural = "Типи запитів техпідтримки"

    def __str__(self):
        return self.title


class SupportTicket(models.Model):
    STATUS_NEW = 'new'
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_CLOSED = 'closed'

    STATUS_CHOICES = (
        (STATUS_NEW, 'Не в роботі'),
        (STATUS_IN_PROGRESS, 'В роботі'),
        (STATUS_CLOSED, 'Закрито'),
    )

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='support_tickets')
    subject = models.CharField(max_length=255, blank=True)
    preset = models.ForeignKey(SupportPresetQuestion, on_delete=models.SET_NULL, null=True, blank=True)
    # optional link to a purchased Ticket (used for refund requests)
    ticket = models.ForeignKey('Ticket', on_delete=models.SET_NULL, null=True, blank=True, related_name='support_requests')
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_NEW)
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_support_tickets')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_message_at = models.DateTimeField(null=True, blank=True)
    is_archived = models.BooleanField(default=False)

    class Meta:
        ordering = ['-last_message_at', '-created_at']
        verbose_name = "Звернення в техпідтримку"
        verbose_name_plural = "Звернення в техпідтримку"

    def __str__(self):
        return f"#{self.id} — {self.user.username} — {self.get_status_display()}"


class SupportMessage(models.Model):
    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    text = models.TextField()
    attachment = models.ImageField(upload_to='support_messages/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)
    is_from_admin = models.BooleanField(default=False)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"Повідомлення #{self.id} — Звернення #{self.ticket_id}"


@receiver(post_save, sender=SupportMessage)
def update_ticket_last_message(sender, instance, created, **kwargs):
    if created:
        try:
            ticket = instance.ticket
            ticket.last_message_at = instance.created_at
            ticket.save(update_fields=['last_message_at', 'updated_at'])
        except Exception:
            pass


class SupportWorker(models.Model):
    """Administrative model for support staff accounts.

    Creating a SupportWorker will create a linked `User` account (if not provided)
    with `is_staff=True` so the worker can access the support admin UI.
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True, related_name='support_worker')
    username = models.CharField(max_length=150, blank=True, help_text='Логін для створення користувача (необов\'язково)')
    email = models.EmailField(blank=True)
    full_name = models.CharField(max_length=200, blank=True)
    avatar = models.ImageField(upload_to='support_workers/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Працівник техпідтримки'
        verbose_name_plural = 'Працівники техпідтримки'

    def __str__(self):
        return self.full_name or self.username or (self.user.username if self.user else 'SupportWorker')

    def save(self, *args, **kwargs):
        # If user not set, create one automatically
        if not self.user:
            base_username = (self.username or (self.email.split('@')[0] if self.email else 'support'))[:140]
            username = base_username
            n = 1
            while User.objects.filter(username=username).exists():
                username = f"{base_username}{n}"
                n += 1

            # generate a short random password
            import secrets, string
            alphabet = string.ascii_letters + string.digits
            raw_pw = ''.join(secrets.choice(alphabet) for _ in range(10))

            # store raw password temporarily on the instance so admin can display it if needed
            try:
                self._raw_password = raw_pw
            except Exception:
                self._raw_password = None

            user = User.objects.create_user(username=username, email=self.email or '', password=raw_pw)
            user.is_staff = True
            user.save()
            self.user = user

            # try to email credentials to the worker (best-effort)
            try:
                from .email_utils import send_email
                subject = 'Акаунт для техпідтримки — Dieller Bus'
                body = f"Привіт\n\nБуло створено акаунт для техпідтримки. Логін: {user.username}\nПароль: {raw_pw}\n\nЗмініть пароль після входу."
                send_email(subject, body, [self.email], from_email=settings.DEFAULT_FROM_EMAIL, fail_silently=True, async_send=True)
            except Exception:
                pass

        # save SupportWorker instance
        super().save(*args, **kwargs)

        # if an avatar was provided for the support worker, copy it to the created user's profile
        try:
            if getattr(self, 'avatar', None) and getattr(self, 'user', None):
                profile = getattr(self.user, 'profile', None)
                if profile:
                    profile.avatar = self.avatar
                    profile.save(update_fields=['avatar'])
        except Exception:
            pass


class Carrier(models.Model):
    """Administrative model for carrier accounts.

    Creating a Carrier will create a linked `User` account (if not provided)
    with `is_staff=True` so the carrier user can access the restricted admin
    views for managing their own trips and viewing sold tickets.
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True, related_name='carrier_account')
    username = models.CharField(max_length=150, blank=True, help_text='Логін для створення користувача (необов\'язково)')
    email = models.EmailField(blank=True)
    company_name = models.CharField(max_length=255, blank=True)
    business_number = models.CharField(max_length=64, blank=True, verbose_name='ФОП/ТОВ')
    phone = models.CharField(max_length=32, blank=True)
    avatar = models.ImageField(upload_to='carriers/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Перевізник'
        verbose_name_plural = 'Перевізники'

    def __str__(self):
        return self.company_name or self.username or (self.user.username if self.user else 'Carrier')

    def save(self, *args, **kwargs):
        # If user not set, create one automatically
        if not self.user:
            base_username = (self.username or (self.email.split('@')[0] if self.email else 'carrier'))[:140]
            username = base_username
            n = 1
            while User.objects.filter(username=username).exists():
                username = f"{base_username}{n}"
                n += 1

            # generate a short random password
            import secrets, string
            alphabet = string.ascii_letters + string.digits
            raw_pw = ''.join(secrets.choice(alphabet) for _ in range(10))

            # store raw password temporarily on the instance so admin can display it if needed
            try:
                self._raw_password = raw_pw
            except Exception:
                self._raw_password = None

            user = User.objects.create_user(username=username, email=self.email or '', password=raw_pw)
            user.is_staff = True
            user.save()
            self.user = user

            # try to email credentials to the carrier (best-effort)
            try:
                from .email_utils import send_email
                subject = 'Акаунт перевізника — Dieller Bus'
                body = f"Привіт\n\nБуло створено акаунт перевізника. Логін: {user.username}\nПароль: {raw_pw}\n\nЗмініть пароль після входу."
                send_email(subject, body, [self.email], from_email=settings.DEFAULT_FROM_EMAIL, fail_silently=True, async_send=True)
            except Exception:
                pass

        # save Carrier instance
        super().save(*args, **kwargs)

        # mark linked profile as carrier
        try:
            if getattr(self, 'user', None) and getattr(self.user, 'profile', None):
                p = self.user.profile
                p.is_carrier = True
                p.save(update_fields=['is_carrier'])
        except Exception:
            pass


class SiteConfig(models.Model):
    """Simple site-wide configuration/singleton storing store name, address and contacts."""
    shop_name = models.CharField(max_length=255, default='Dieller Bus', help_text='Найменування магазину / сервісу')
    shop_address = models.CharField(max_length=255, blank=True, help_text='Юридична або поштова адреса')
    contact_email = models.EmailField(blank=True, help_text='Контактний email')
    contact_phone = models.CharField(max_length=32, blank=True, help_text='Контактний телефон')
    owner_info = models.TextField(blank=True, help_text='Інформація про власника (ФОП/ТОВ, реквізити для відшкодувань тощо)')
    currency = models.CharField(max_length=10, default='UAH')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Налаштування сайту'
        verbose_name_plural = 'Налаштування сайту'

    def __str__(self):
        return self.shop_name or 'SiteConfig'

    @classmethod
    def get_solo(cls):
        obj = cls.objects.first()
        if obj is None:
            obj = cls.objects.create(shop_name='Dieller Bus')
        return obj


class StaticPage(models.Model):
    """CMS-like simple static page stored in DB so non-developers can edit content via admin."""
    slug = models.SlugField(max_length=200, unique=True)
    title = models.CharField(max_length=255)
    content = models.TextField(blank=True, help_text='HTML-контент сторінки')
    is_published = models.BooleanField(default=True)
    language = models.CharField(max_length=10, default='uk', help_text='Код мови сторінки')
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('order', 'title')
        verbose_name = 'Статична сторінка'
        verbose_name_plural = 'Статичні сторінки'

    def __str__(self):
        return f"{self.title} ({self.slug})"

    def get_absolute_url(self):
        try:
            return reverse('main:static_page', args=(self.slug,))
        except Exception:
            return f"/page/{self.slug}/"

        # if an avatar was provided for the carrier, copy it to the created user's profile
        try:
            if getattr(self, 'avatar', None) and getattr(self, 'user', None):
                profile = getattr(self.user, 'profile', None)
                if profile:
                    profile.avatar = self.avatar
                    profile.save(update_fields=['avatar'])
        except Exception:
            pass

