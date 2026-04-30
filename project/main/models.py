from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

# Create your models here.

class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    phone = models.CharField(max_length=20, blank=True, null=True)
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
class EmailVerification(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    code = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} - {self.code}"



class Ticket(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    # legacy/free-text route field
    route = models.CharField(max_length=255, default="", blank=True)

    # explicit ticket fields (used by views)
    from_city = models.CharField(max_length=200, blank=True, null=True)
    to_city = models.CharField(max_length=200, blank=True, null=True)
    travel_date = models.DateField(blank=True, null=True)
    passengers = models.IntegerField(default=1)
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    paid = models.BooleanField(default=False)
    stripe_session_id = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        user = getattr(self.user, 'username', 'unknown')
        if self.from_city and self.to_city:
            return f"{user} | {self.from_city} -> {self.to_city} | {self.total_price} грн"
        return f"{user} | {self.route} | {self.total_price} грн"


# --- New models for routes, cities, trips ---
class City(models.Model):
    name = models.CharField(max_length=200)
    country = models.CharField(max_length=100, blank=True)

    class Meta:
        verbose_name = "Місто"
        verbose_name_plural = "Міста"

    def __str__(self):
        return self.name


class Route(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)

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
    ]

    route = models.ForeignKey(Route, on_delete=models.CASCADE, related_name='trips')
    title = models.CharField(max_length=255, blank=True)
    carrier = models.CharField(max_length=255, blank=True, default='')
    date = models.DateField(blank=True, null=True)
    seats = models.PositiveIntegerField(default=50)
    base_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    currency = models.CharField(max_length=10, default='UAH')
    start_city = models.ForeignKey('City', on_delete=models.SET_NULL, null=True, blank=True, related_name='starting_trips')
    end_city = models.ForeignKey('City', on_delete=models.SET_NULL, null=True, blank=True, related_name='ending_trips')
    discount_percent = models.PositiveIntegerField(default=0)
    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES, default='UA_PL')
    active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Рейс"
        verbose_name_plural = "Рейси"

    def __str__(self):
        return f"{self.route.name} — {self.title or self.date or self.id}"


class TripStop(models.Model):
    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name='trip_stops')
    city = models.ForeignKey(City, on_delete=models.CASCADE)
    order = models.PositiveIntegerField(default=0)
    arrival_time = models.TimeField(blank=True, null=True)
    departure_time = models.TimeField(blank=True, null=True)
    address = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['order']
        verbose_name = "Зупинка рейсу"
        verbose_name_plural = "Зупинки рейсу"

    def __str__(self):
        return f"{self.trip} — {self.city.name} ({self.order})"


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