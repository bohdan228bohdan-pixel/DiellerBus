from django.test import TestCase
from django.test import RequestFactory
from .models import City, Route, Trip, TripStop, TripFare, TripDayAvailability
from .views import api_trips


class SubcityMatchingTests(TestCase):
	def setUp(self):
		# create cities
		self.main = City.objects.create(name='Луцьк', country='UA')
		self.sub = City.objects.create(name='Копачівка', country='UA', parent=self.main)
		self.pl = City.objects.create(name='Пулави', country='PL')

		# route with include_subcities True
		self.route = Route.objects.create(name='Луцьк — Пулави', include_subcities=True)

		# trip
		self.trip = Trip.objects.create(route=self.route, title='Тест', direction='UA_PL', seats=30, base_price=1000.0)
		TripStop.objects.create(trip=self.trip, city=self.main, order=1, departure_time='08:00')
		TripStop.objects.create(trip=self.trip, city=self.pl, order=2, arrival_time='17:00')

		# explicit fare defined for main->pl
		TripFare.objects.create(trip=self.trip, from_city=self.main, to_city=self.pl, price=1100.0)

		# availability for specific date
		TripDayAvailability.objects.create(trip=self.trip, date='2026-06-25', available=True)

	def test_subcity_resolves_to_main_fare(self):
		rf = RequestFactory()
		req = rf.get('/api/trips/?from=Копачівка&to=Пулави&date=2026-06-25')
		res = api_trips(req)
		# Django JsonResponse doesn't have .json(); decode content
		import json as _json
		data = _json.loads(res.content.decode('utf-8'))
		self.assertTrue('trips' in data)
		self.assertEqual(len(data['trips']), 1)
		trip = data['trips'][0]
		self.assertEqual(trip['id'], self.trip.id)
		self.assertEqual(trip['price'], 1100.0)
