from datetime import date, timedelta

from django.test import TestCase
from django.test import RequestFactory, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from .models import City, Route, Trip, TripStop, TripFare, TripDayAvailability, Ticket, Payment
from .views import api_trips

User = get_user_model()


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


class CheckoutPhoneValidationTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username='buyer', email='buyer@example.com', password='pass1234')
		self.city1 = City.objects.create(name='Львів', country='UA')
		self.city2 = City.objects.create(name='Київ', country='UA')
		self.route = Route.objects.create(name='Львів — Київ', active=True)
		self.trip = Trip.objects.create(
			route=self.route,
			title='Тестовий рейс',
			seats=20,
			base_price=100.0,
			start_city=self.city1,
			end_city=self.city2,
			active=True,
		)
		TripStop.objects.create(trip=self.trip, city=self.city1, order=1, departure_time='08:00')
		TripStop.objects.create(trip=self.trip, city=self.city2, order=2, arrival_time='12:00')

	def test_checkout_requires_phone(self):
		self.client.force_login(self.user)
		response = self.client.post(
			reverse('main:checkout', args=[self.trip.id]),
			{
				'passengers': '1',
				'email': 'buyer@example.com',
				'date': (date.today() + timedelta(days=1)).strftime('%Y-%m-%d'),
				'passenger_first': ['Іван'],
				'passenger_last': ['Іваненко'],
				'accept_agreements': 'on',
			},
		)
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Будь ласка, вкажіть номер телефону')


@override_settings(WAYFORPAY_MERCHANT_LOGIN='test-merchant', WAYFORPAY_MERCHANT_SECRET='test-secret')
class WayForPayCheckoutTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username='payer', email='payer@example.com', password='pass1234')
		self.city1 = City.objects.create(name='Львів', country='UA')
		self.city2 = City.objects.create(name='Київ', country='UA')
		self.route = Route.objects.create(name='Львів — Київ', active=True)
		self.trip = Trip.objects.create(
			route=self.route,
			title='Тестовий рейс',
			seats=20,
			base_price=100.0,
			start_city=self.city1,
			end_city=self.city2,
			active=True,
		)
		TripStop.objects.create(trip=self.trip, city=self.city1, order=1, departure_time='08:00')
		TripStop.objects.create(trip=self.trip, city=self.city2, order=2, arrival_time='12:00')

	def test_checkout_renders_wayforpay_form(self):
		self.client.force_login(self.user)
		response = self.client.post(
			reverse('main:checkout', args=[self.trip.id]),
			{
				'passengers': '1',
				'email': 'payer@example.com',
				'phone': '+380501112233',
				'date': (date.today() + timedelta(days=1)).strftime('%Y-%m-%d'),
				'passenger_first': ['Іван'],
				'passenger_last': ['Іваненко'],
				'accept_agreements': 'on',
			},
		)
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'wayforpay')
		ticket = Ticket.objects.get(user=self.user)
		self.assertTrue(ticket.payments.exists())
		payment = ticket.payments.first()
		self.assertEqual(payment.provider, 'wayforpay')
		self.assertEqual(payment.status, 'pending')

	def test_create_ticket_renders_wayforpay_form(self):
		self.client.force_login(self.user)
		response = self.client.post(
			reverse('main:create_ticket'),
			{
				'from': 'Львів',
				'to': 'Київ',
				'passengers': '1',
				'date': (date.today() + timedelta(days=1)).strftime('%Y-%m-%d'),
				'price': '100',
				'email': 'payer@example.com',
				'phone': '+380501112233',
			},
		)
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'wayforpay')
		ticket = Ticket.objects.get(user=self.user)
		self.assertTrue(ticket.payments.exists())
		payment = ticket.payments.first()
		self.assertEqual(payment.provider, 'wayforpay')
		self.assertEqual(payment.status, 'pending')
