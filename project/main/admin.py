from django.contrib import admin
from .models import Profile

# Register your models here.

@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'balance', 'phone', 'created_at']
    search_fields = ['user__username', 'user__email', 'phone']
    list_filter = ['created_at']

from .models import City, Route, RouteStop, Trip, TripStop


class RouteStopInline(admin.TabularInline):
    model = RouteStop
    extra = 1
    fields = ('order', 'city', 'address')


@admin.register(Route)
class RouteAdmin(admin.ModelAdmin):
    list_display = ('name', 'active')
    search_fields = ('name',)
    inlines = [RouteStopInline]


class TripStopInline(admin.TabularInline):
    model = TripStop
    extra = 1
    fields = ('order', 'city', 'arrival_time', 'departure_time', 'address')


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = ('title', 'carrier', 'route', 'date', 'direction', 'base_price', 'currency', 'discount_percent', 'seats', 'active')
    list_filter = ('date', 'direction', 'active')
    search_fields = ('title', 'route__name', 'carrier')
    inlines = [TripStopInline]


@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    list_display = ('name', 'country')
    search_fields = ('name',)
