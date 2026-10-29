import hashlib
import hmac
from datetime import date, timedelta
from unittest.mock import patch

from django.test import TestCase, RequestFactory, Client, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.core import mail
from .models import City, Route, Trip, TripStop, TripFare, TripDayAvailability, Ticket, Payment, Profile, Carrier
from .views import api_trips, _generate_ticket_pdf_bytes, _render_wayforpay_form, _send_ticket_email
from .wayforpay import WayForPayService, create_wayforpay_invoice, _build_signature

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


@override_settings(WAYFORPAY_MERCHANT_LOGIN='test-merchant', WAYFORPAY_MERCHANT_SECRET='test-secret')
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
class DirectCheckoutRedirectTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username='directpayer', email='directpayer@example.com', password='pass1234')
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
		Profile.objects.update_or_create(user=self.user, defaults={'phone': '+380501112233'})

	def test_get_checkout_with_profile_phone_renders_wayforpay_form(self):
		self.client.force_login(self.user)
		response = self.client.get(
			reverse('main:checkout', args=[self.trip.id]),
			{
				'pax': '1',
				'date': (date.today() + timedelta(days=1)).strftime('%Y-%m-%d'),
				'from': 'Львів',
				'to': 'Київ',
			},
		)
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'wayforpay')
		ticket = Ticket.objects.get(user=self.user)
		self.assertTrue(ticket.payments.exists())
		payment = ticket.payments.first()
		self.assertEqual(payment.provider, 'wayforpay')
		self.assertEqual(payment.status, 'pending')


@override_settings(WAYFORPAY_MERCHANT_LOGIN='test-merchant', WAYFORPAY_MERCHANT_SECRET='test-secret')
class TicketPdfAndPaymentFallbackTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username='pdfuser', email='pdf@example.com', password='pass1234')
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

	def test_generate_ticket_pdf_bytes_does_not_crash(self):
		ticket = Ticket.objects.create(
			user=self.user,
			trip=self.trip,
			from_city='Львів',
			to_city='Київ',
			travel_date=date.today() + timedelta(days=1),
			passengers=1,
			total_price='100.00',
			currency='UAH',
			paid=True,
		)
		pdf_bytes = _generate_ticket_pdf_bytes(ticket, RequestFactory().get('/'))
		self.assertTrue(pdf_bytes)
		self.assertTrue(pdf_bytes.startswith(b'%PDF'))

	def test_generate_ticket_pdf_bytes_handles_missing_optional_details(self):
		ticket = Ticket.objects.create(
			user=self.user,
			trip=self.trip,
			from_city='Львів',
			to_city='Київ',
			travel_date=date.today() + timedelta(days=1),
			passengers=1,
			total_price='100.00',
			currency='UAH',
			paid=False,
		)
		pdf_bytes = _generate_ticket_pdf_bytes(ticket, RequestFactory().get('/'))
		self.assertTrue(pdf_bytes)
		self.assertTrue(pdf_bytes.startswith(b'%PDF'))

	def test_generate_ticket_pdf_includes_travel_date(self):
		travel_date = date.today() + timedelta(days=2)
		ticket = Ticket.objects.create(
			user=self.user,
			trip=self.trip,
			from_city='Львів',
			to_city='Київ',
			travel_date=travel_date,
			passengers=1,
			total_price='100.00',
			currency='UAH',
			paid=True,
		)
		pdf_bytes = _generate_ticket_pdf_bytes(ticket, RequestFactory().get('/'))
		self.assertTrue(pdf_bytes)
		self.assertTrue(pdf_bytes.startswith(b'%PDF'))

	def test_send_ticket_email_uses_contact_email_when_user_email_missing(self):
		user = User.objects.create_user(username='noemail', email='', password='pass1234')
		ticket = Ticket.objects.create(
			user=user,
			trip=self.trip,
			from_city='Львів',
			to_city='Київ',
			travel_date=date.today() + timedelta(days=3),
			passengers=1,
			total_price='100.00',
			currency='UAH',
			paid=True,
			contact_email='contact@example.com',
		)
		mail.outbox = []
		result = _send_ticket_email(ticket, None)
		self.assertTrue(result)
		self.assertEqual(len(mail.outbox), 1)
		self.assertEqual(mail.outbox[0].to, ['contact@example.com'])

	def test_generate_ticket_pdf_uses_business_type_label(self):
		carrier_user = User.objects.create_user(username='carrieruser', email='carrier@example.com', password='pass1234')
		carrier = Carrier.objects.create(
			user=carrier_user,
			company_name='Тестовий перевізник',
			business_type='FOP',
			business_number='1234567890',
			phone='+380501234567',
		)
		self.trip.carrier_user = carrier_user
		self.trip.save(update_fields=['carrier_user'])
		ticket = Ticket.objects.create(
			user=self.user,
			trip=self.trip,
			from_city='Львів',
			to_city='Київ',
			travel_date=date.today() + timedelta(days=3),
			passengers=1,
			total_price='100.00',
			currency='UAH',
			paid=True,
		)
		pdf_bytes = _generate_ticket_pdf_bytes(ticket, RequestFactory().get('/'))
		self.assertTrue(pdf_bytes)
		self.assertTrue(pdf_bytes.startswith(b'%PDF'))

	def test_render_wayforpay_form_falls_back_to_gateway_form(self):
		class DummyService:
			def create_invoice(self, **kwargs):
				return {'merchantAccount': 'test-merchant', 'merchantDomainName': 'example.com'}

		ticket = Ticket.objects.create(user=self.user, route='Тест', total_price='100.00', currency='UAH', paid=False)
		payment = Payment.objects.create(ticket=ticket, user=self.user, provider='wayforpay', amount='100.00', currency='UAH', status='pending')
		request = RequestFactory().get('/', HTTP_HOST='testserver')
		with patch('main.views.get_wayforpay_service', return_value=DummyService()):
			response = _render_wayforpay_form(request, ticket, payment, contact_email='pdf@example.com', contact_phone='+380501112233')
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'https://secure.wayforpay.com/page')
		self.assertContains(response, 'merchantAccount')
		self.assertContains(response, 'apiVersion')
		self.assertContains(response, 'transactionType')
		self.assertContains(response, 'form.submit()')
		self.assertContains(response, 'name="serviceUrl"')
		self.assertContains(response, 'name="returnUrl"')

	def test_create_wayforpay_invoice_uses_api_payload(self):
		class DummyResponse:
			status_code = 200
			content = b'{"invoiceUrl":"https://secure.wayforpay.com/pay/test","reasonCode":1100}'
			def json(self):
				return {"invoiceUrl":"https://secure.wayforpay.com/pay/test","reasonCode":1100}
			def raise_for_status(self):
				return None

		with patch('main.wayforpay.requests.post', return_value=DummyResponse()) as mocked_post:
			result = create_wayforpay_invoice(
				order_reference='ticket-1',
				amount='100.00',
				currency='UAH',
				product_names=['Ticket'],
				product_counts=['1'],
				product_prices=['100.00'],
				merchant_login='merchant',
				merchant_secret='secret',
				merchant_domain='example.com',
				return_url='https://example.com/success',
				service_url='https://example.com/callback',
			)
		self.assertEqual(result['invoiceUrl'], 'https://secure.wayforpay.com/pay/test')
		self.assertEqual(mocked_post.call_count, 1)

	def test_wayforpay_signature_uses_hmac_md5_secret_format(self):
		service = WayForPayService(merchant_login='merchant', merchant_secret='secret', merchant_domain='example.com')
		signature = service.build_signature(
			'merchant',
			'example.com',
			'ticket-42',
			'100.00',
			'UAH',
			['Ticket'],
			['1'],
			['100.00'],
			merchant_secret='secret',
			order_date='1700000000',
		)
		message = ';'.join(['merchant', 'example.com', 'ticket-42', '1700000000', '100.00', 'UAH', 'Ticket', '1', '100.00'])
		expected = hmac.new(b'secret', message.encode('utf-8'), hashlib.md5).hexdigest()
		self.assertEqual(signature, expected)

	def test_wayforpay_signature_flattens_array_fields(self):
		service = WayForPayService(merchant_login='merchant', merchant_secret='secret', merchant_domain='example.com')
		signature = service.build_signature(
			'merchant',
			'example.com',
			'ticket-42',
			'100.00',
			'UAH',
			['Ticket A', 'Ticket B'],
			['1', '2'],
			['50.00', '50.00'],
			merchant_secret='secret',
			order_date='1700000000',
		)
		message = ';'.join(['merchant', 'example.com', 'ticket-42', '1700000000', '100.00', 'UAH', 'Ticket A', 'Ticket B', '1', '2', '50.00', '50.00'])
		expected = hmac.new(b'secret', message.encode('utf-8'), hashlib.md5).hexdigest()
		self.assertEqual(signature, expected)

	def test_wayforpay_verify_signature_accepts_callback_signature(self):
		service = WayForPayService(merchant_login='merchant', merchant_secret='secret', merchant_domain='example.com')
		data = {
			'orderReference': 'ticket-42',
			'status': 'accept',
			'time': '1700000000',
		}
		message = ';'.join(['ticket-42', 'accept', '1700000000'])
		signature = hmac.new(b'secret', message.encode('utf-8'), hashlib.md5).hexdigest()
		self.assertTrue(service.verify_signature(signature, data))

	def test_wayforpay_service_builds_invoice_payload_and_callback_response(self):
		service = WayForPayService(merchant_login='merchant', merchant_secret='secret', merchant_domain='example.com')
		payload = service.build_invoice_payload(
			order_reference='ticket-42',
			amount='100.00',
			currency='UAH',
			product_names=['Ticket'],
			product_counts=['1'],
			product_prices=['100.00'],
			return_url='https://example.com/success',
			service_url='https://example.com/callback',
		)
		self.assertEqual(payload['merchantAccount'], 'merchant')
		self.assertEqual(payload['merchantAuthType'], 'SimpleSignature')
		self.assertTrue(payload['merchantSignature'])
		self.assertEqual(payload.get('apiVersion'), '1')
		self.assertEqual(payload.get('transactionType'), 'CREATE_INVOICE')
		callback_response = service.build_callback_response('ticket-42', status='accept', timestamp=1700000000)
		self.assertEqual(callback_response['orderReference'], 'ticket-42')
		self.assertEqual(callback_response['status'], 'accept')
		message = ';'.join(['ticket-42', 'accept', str(1700000000)])
		expected_signature = hmac.new(b'secret', message.encode('utf-8'), hashlib.md5).hexdigest()
		self.assertEqual(callback_response['signature'], expected_signature)

	def test_create_wayforpay_invoice_returns_error_details_on_api_failure(self):
		class DummyResponse:
			status_code = 400
			text = 'invalid merchant'
			content = b'invalid merchant'
			def json(self):
				return {'message': 'invalid merchant'}
			def raise_for_status(self):
				raise Exception('boom')

		with patch('main.wayforpay.requests.post', return_value=DummyResponse()):
			result = create_wayforpay_invoice(
				order_reference='ticket-99',
				amount='100.00',
				currency='UAH',
				product_names=['Ticket'],
				product_counts=['1'],
				product_prices=['100.00'],
				merchant_login='merchant',
				merchant_secret='secret',
				merchant_domain='example.com',
				return_url='https://example.com/success',
				service_url='https://example.com/callback',
			)
		self.assertEqual(result['status_code'], 400)
		self.assertIn('invalid merchant', result['response_text'])
		self.assertEqual(result['error'], 'WayForPay API request failed')

	def test_checkout_redirects_to_wayforpay_invoice(self):
		class DummyResponse:
			status_code = 200
			content = b'{"invoiceUrl":"https://secure.wayforpay.com/pay/test","reasonCode":1100}'
			def json(self):
				return {"invoiceUrl":"https://secure.wayforpay.com/pay/test","reasonCode":1100}
			def raise_for_status(self):
				return None

		self.client.force_login(self.user)
		with patch('main.wayforpay.requests.post', return_value=DummyResponse()):
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
		self.assertEqual(response.status_code, 302)
		self.assertEqual(response['Location'], 'https://secure.wayforpay.com/pay/test')
		ticket = Ticket.objects.get(user=self.user)
		self.assertTrue(ticket.payments.exists())
		payment = ticket.payments.first()
		self.assertEqual(payment.provider, 'wayforpay')
		self.assertEqual(payment.status, 'pending')

	def test_checkout_with_csrf_token_succeeds(self):
		class DummyResponse:
			status_code = 200
			content = b'{"invoiceUrl":"https://secure.wayforpay.com/pay/test","reasonCode":1100}'
			def json(self):
				return {"invoiceUrl":"https://secure.wayforpay.com/pay/test","reasonCode":1100}
			def raise_for_status(self):
				return None

		client = Client(enforce_csrf_checks=True)
		client.force_login(self.user)
		get_response = client.get(
			reverse('main:checkout', args=[self.trip.id]),
			{
				'pax': '1',
				'date': (date.today() + timedelta(days=1)).strftime('%Y-%m-%d'),
				'from': 'Львів',
				'to': 'Київ',
			},
		)
		self.assertEqual(get_response.status_code, 200)
		csrf_token = get_response.cookies['csrftoken'].value

		with patch('main.wayforpay.requests.post', return_value=DummyResponse()):
			response = client.post(
				reverse('main:checkout', args=[self.trip.id]),
				{
					'csrfmiddlewaretoken': csrf_token,
					'passengers': '1',
					'email': 'payer@example.com',
					'phone': '+380501112233',
					'date': (date.today() + timedelta(days=1)).strftime('%Y-%m-%d'),
					'passenger_first': ['Іван'],
					'passenger_last': ['Іваненко'],
					'accept_agreements': 'on',
				},
			)
		self.assertEqual(response.status_code, 302)
		self.assertEqual(response['Location'], 'https://secure.wayforpay.com/pay/test')

	def test_create_ticket_redirects_to_wayforpay_invoice(self):
		class DummyResponse:
			status_code = 200
			content = b'{"invoiceUrl":"https://secure.wayforpay.com/pay/test","reasonCode":1100}'
			def json(self):
				return {"invoiceUrl":"https://secure.wayforpay.com/pay/test","reasonCode":1100}
			def raise_for_status(self):
				return None

		self.client.force_login(self.user)
		with patch('main.wayforpay.requests.post', return_value=DummyResponse()):
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
		self.assertEqual(response.status_code, 302)
		self.assertEqual(response['Location'], 'https://secure.wayforpay.com/pay/test')
		ticket = Ticket.objects.get(user=self.user)
		self.assertTrue(ticket.payments.exists())
		payment = ticket.payments.first()
		self.assertEqual(payment.provider, 'wayforpay')
		self.assertEqual(payment.status, 'pending')

	@override_settings(WAYFORPAY_MERCHANT_LOGIN='test-merchant', WAYFORPAY_MERCHANT_SECRET='test-secret')
	def test_callback_marks_payment_as_success(self):
		ticket = Ticket.objects.create(user=self.user, route='Тест', total_price='100.00', currency='UAH', paid=False)
		payment = Payment.objects.create(ticket=ticket, user=self.user, provider='wayforpay', amount='100.00', currency='UAH', status='pending')
		payload = {
			'merchantAccount': 'test-merchant',
			'merchantDomainName': 'example.com',
			'orderReference': f'ticket-{ticket.id}-1234567890',
			'amount': '100.00',
			'currency': 'UAH',
			'productName': 'Ticket',
			'productCount': '1',
			'productPrice': '100.00',
			'reasonCode': '1100',
			'transactionId': 'tx-123',
		}
		payload['merchantSignature'] = _build_signature([
			payload['merchantAccount'],
			payload['merchantDomainName'],
			payload['orderReference'],
			payload['amount'],
			payload['currency'],
			payload['productName'],
			payload['productCount'],
			payload['productPrice'],
		], 'test-secret')
		response = self.client.post(reverse('main:wayforpay_callback'), payload)
		self.assertEqual(response.status_code, 200)
		payment.refresh_from_db()
		ticket.refresh_from_db()
		self.assertEqual(payment.status, 'success')
		self.assertTrue(ticket.paid)
		self.assertEqual(payment.provider_payment_id, 'tx-123')

	@override_settings(WAYFORPAY_MERCHANT_LOGIN='test-merchant', WAYFORPAY_MERCHANT_SECRET='test-secret')
	def test_callback_accepts_product_array_field_names(self):
		ticket = Ticket.objects.create(user=self.user, route='Тест', total_price='100.00', currency='UAH', paid=False)
		payment = Payment.objects.create(ticket=ticket, user=self.user, provider='wayforpay', amount='100.00', currency='UAH', status='pending')
		payload = {
			'merchantAccount': 'test-merchant',
			'merchantDomainName': 'example.com',
			'orderReference': f'ticket-{ticket.id}-1234567890',
			'amount': '100.00',
			'currency': 'UAH',
			'productName[]': ['Ticket'],
			'productCount[]': ['1'],
			'productPrice[]': ['100.00'],
			'reasonCode': '1100',
			'transactionId': 'tx-123',
		}
		payload['merchantSignature'] = _build_signature([
			payload['merchantAccount'],
			payload['merchantDomainName'],
			payload['orderReference'],
			payload['amount'],
			payload['currency'],
			payload['productName[]'],
			payload['productCount[]'],
			payload['productPrice[]'],
		], 'test-secret')
		response = self.client.post(reverse('main:wayforpay_callback'), payload)
		self.assertEqual(response.status_code, 200)
		payment.refresh_from_db()
		ticket.refresh_from_db()
		self.assertEqual(payment.status, 'success')
		self.assertTrue(ticket.paid)
		self.assertEqual(payment.provider_payment_id, 'tx-123')

	@override_settings(WAYFORPAY_MERCHANT_LOGIN='test-merchant', WAYFORPAY_MERCHANT_SECRET='test-secret')
	def test_callback_accepts_status_signature(self):
		ticket = Ticket.objects.create(user=self.user, route='Тест', total_price='100.00', currency='UAH', paid=False)
		payment = Payment.objects.create(ticket=ticket, user=self.user, provider='wayforpay', amount='100.00', currency='UAH', status='pending')
		payload = {
			'merchantAccount': 'test-merchant',
			'merchantDomainName': 'example.com',
			'orderReference': f'ticket-{ticket.id}-1234567890',
			'status': 'accept',
			'time': '1700000000',
			'transactionId': 'tx-456',
		}
		payload['merchantSignature'] = _build_signature([
			payload['orderReference'],
			payload['status'],
			payload['time'],
		], 'test-secret')
		response = self.client.post(reverse('main:wayforpay_callback'), payload)
		self.assertEqual(response.status_code, 200)
		payment.refresh_from_db()
		ticket.refresh_from_db()
		self.assertEqual(payment.status, 'success')
		self.assertTrue(ticket.paid)
		self.assertEqual(payment.provider_payment_id, 'tx-456')

	@override_settings(WAYFORPAY_MERCHANT_LOGIN='test-merchant', WAYFORPAY_MERCHANT_SECRET='test-secret')
	def test_payment_success_accepts_product_array_field_names(self):
		ticket = Ticket.objects.create(user=self.user, route='Тест', total_price='100.00', currency='UAH', paid=False)
		payload = {
			'orderReference': f'ticket-{ticket.id}-1234567890',
			'status': 'accept',
			'time': '1700000000',
			'productName[]': ['Ticket'],
			'productCount[]': ['1'],
			'productPrice[]': ['100.00'],
		}
		payload['merchantSignature'] = _build_signature([
			payload['orderReference'],
			payload['status'],
			payload['time'],
		], 'test-secret')
		response = self.client.get(reverse('main:payment_success'), payload)
		self.assertEqual(response.status_code, 200)
		ticket.refresh_from_db()
		self.assertTrue(ticket.paid)
		self.assertTrue(ticket.payments.exists())
		self.assertEqual(ticket.payments.latest('created_at').status, 'success')

	@override_settings(WAYFORPAY_MERCHANT_LOGIN='test-merchant', WAYFORPAY_MERCHANT_SECRET='test-secret')
	def test_payment_success_allows_unsigned_return_when_last_ticket_matches_session(self):
		ticket = Ticket.objects.create(user=self.user, route='Тест', total_price='100.00', currency='UAH', paid=False)
		payment = Payment.objects.create(ticket=ticket, user=self.user, provider='wayforpay', amount='100.00', currency='UAH', status='pending')
		session = self.client.session
		session['last_ticket_id'] = ticket.id
		session.save()
		response = self.client.get(reverse('main:payment_success'), {
			'orderReference': f'ticket-{ticket.id}-1234567890',
			'status': 'accept',
			'transaction_id': 'tx-session-1',
		})
		self.assertEqual(response.status_code, 200)
		ticket.refresh_from_db()
		self.assertTrue(ticket.paid)
		self.assertTrue(ticket.payments.exists())
		self.assertEqual(ticket.payments.latest('created_at').status, 'success')
		self.assertEqual(ticket.payments.latest('created_at').provider_payment_id, 'tx-session-1')

	@override_settings(WAYFORPAY_MERCHANT_LOGIN='test-merchant', WAYFORPAY_MERCHANT_SECRET='test-secret')
	def test_payment_success_allows_unsigned_return_with_reasoncode_1100(self):
		self.client.force_login(self.user)
		ticket = Ticket.objects.create(user=self.user, route='Тест', total_price='100.00', currency='UAH', paid=False)
		Payment.objects.create(ticket=ticket, user=self.user, provider='wayforpay', amount='100.00', currency='UAH', status='pending')
		response = self.client.get(reverse('main:payment_success'), {
			'orderReference': f'ticket-{ticket.id}-1234567890',
			'reasonCode': '1100',
			'transaction_id': 'tx-session-1100',
		})
		self.assertEqual(response.status_code, 200)
		ticket.refresh_from_db()
		self.assertTrue(ticket.paid)
		self.assertTrue(ticket.payments.exists())
		self.assertEqual(ticket.payments.latest('created_at').status, 'success')
		self.assertEqual(ticket.payments.latest('created_at').provider_payment_id, 'tx-session-1100')

	@override_settings(WAYFORPAY_MERCHANT_LOGIN='test-merchant', WAYFORPAY_MERCHANT_SECRET='test-secret')
	def test_payment_success_marks_pending_wayforpay_ticket_paid_for_same_user(self):
		self.client.force_login(self.user)
		ticket = Ticket.objects.create(user=self.user, route='Тест', total_price='100.00', currency='UAH', paid=False)
		Payment.objects.create(ticket=ticket, user=self.user, provider='wayforpay', amount='100.00', currency='UAH', status='pending')
		response = self.client.get(reverse('main:payment_success'), {
			'orderReference': f'ticket-{ticket.id}-1234567890',
			'status': 'accept',
			'transaction_id': 'tx-session-2',
		})
		self.assertEqual(response.status_code, 200)
		ticket.refresh_from_db()
		self.assertTrue(ticket.paid)
		self.assertTrue(ticket.payments.exists())
		self.assertEqual(ticket.payments.latest('created_at').status, 'success')
		self.assertEqual(ticket.payments.latest('created_at').provider_payment_id, 'tx-session-2')

	@override_settings(WAYFORPAY_MERCHANT_LOGIN='test-merchant', WAYFORPAY_MERCHANT_SECRET='test-secret')
	def test_payment_success_renders_failure_page_for_declined_status(self):
		response = self.client.get(reverse('main:payment_success'), {
			'orderReference': 'ticket-999',
			'reasonCode': '2',
			'transaction_id': 'tx-failed-1',
		}, follow=True)
		self.assertEqual(response.status_code, 200)
		self.assertTemplateUsed(response, 'payment_cancel.html')
		self.assertContains(response, 'Оплата не вдалася')
		self.assertNotContains(response, 'Дякуємо — оплата успішна')

class TripAvailabilityTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username='tripuser', email='tripuser@example.com', password='pass1234')
		self.city1 = City.objects.create(name='Львів', country='UA')
		self.city2 = City.objects.create(name='Київ', country='UA')
		self.route = Route.objects.create(name='Львів — Київ', active=True)
		self.trip = Trip.objects.create(
			route=self.route,
			title='Тестовий рейс',
			seats=5,
			base_price=100.0,
			start_city=self.city1,
			end_city=self.city2,
			active=True,
		)
		TripStop.objects.create(trip=self.trip, city=self.city1, order=1, departure_time='08:00')
		TripStop.objects.create(trip=self.trip, city=self.city2, order=2, arrival_time='12:00')

	def test_api_trips_accounts_for_paid_tickets_when_calculating_free_seats(self):
		Ticket.objects.create(
			user=self.user,
			trip=self.trip,
			from_city='Львів',
			to_city='Київ',
			travel_date=(date.today() + timedelta(days=1)),
			passengers=2,
			total_price='200.00',
			currency='UAH',
			paid=True,
		)
		rf = RequestFactory()
		req = rf.get('/api/trips/', {
			'from': 'Львів',
			'to': 'Київ',
			'date': (date.today() + timedelta(days=1)).strftime('%Y-%m-%d'),
		})
		res = api_trips(req)
		import json as _json
		data = _json.loads(res.content.decode('utf-8'))
		self.assertEqual(len(data['trips']), 1)
		self.assertEqual(data['trips'][0]['free'], 3)

	@override_settings(DEBUG=True)
	def test_payment_success_marks_ticket_paid_from_order_reference_without_signature_in_debug(self):
		ticket = Ticket.objects.create(user=self.user, route='Тест', total_price='100.00', currency='UAH', paid=False)
		response = self.client.get(reverse('main:payment_success'), {
			'orderReference': f'ticket-{ticket.id}-1234567890',
			'status': 'success',
			'transaction_id': 'tx-777',
		})
		self.assertEqual(response.status_code, 200)
		ticket.refresh_from_db()
		self.assertTrue(ticket.paid)
		self.assertTrue(ticket.payments.exists())
		self.assertEqual(ticket.payments.latest('created_at').status, 'success')

	@override_settings(WAYFORPAY_MERCHANT_LOGIN='test-merchant', WAYFORPAY_MERCHANT_SECRET='test-secret')
	def test_payment_success_redirects_to_cancel_and_deletes_ticket_on_failed_signed_return(self):
		ticket = Ticket.objects.create(user=self.user, route='Тест', total_price='100.00', currency='UAH', paid=False)
		payload = {
			'merchantAccount': 'test-merchant',
			'merchantDomainName': 'example.com',
			'orderReference': f'ticket-{ticket.id}',
			'amount': '100.00',
			'currency': 'UAH',
			'productName': 'Ticket',
			'productCount': '1',
			'productPrice': '100.00',
			'reasonCode': '1500',
			'transactionStatus': 'failure',
		}
		payload['merchantSignature'] = _build_signature([
			payload['orderReference'],
			payload['reasonCode'],
		], 'test-secret')
		response = self.client.post(reverse('main:payment_success'), payload)
		self.assertEqual(response.status_code, 200)
		self.assertTemplateUsed(response, 'payment_cancel.html')
		self.assertContains(response, 'Оплата не вдалася')
		self.assertFalse(Ticket.objects.filter(pk=ticket.id).exists())

	@override_settings(WAYFORPAY_MERCHANT_LOGIN='test-merchant', WAYFORPAY_MERCHANT_SECRET='test-secret')
	def test_callback_deletes_failed_ticket_on_wayforpay_failure(self):
		ticket = Ticket.objects.create(user=self.user, route='Тест', total_price='100.00', currency='UAH', paid=False)
		payment = Payment.objects.create(ticket=ticket, user=self.user, provider='wayforpay', amount='100.00', currency='UAH', status='pending')
		payload = {
			'merchantAccount': 'test-merchant',
			'merchantDomainName': 'example.com',
			'orderReference': f'ticket-{ticket.id}',
			'amount': '100.00',
			'currency': 'UAH',
			'productName': 'Ticket',
			'productCount': '1',
			'productPrice': '100.00',
			'reasonCode': '1500',
			'transactionId': 'tx-456',
		}
		payload['merchantSignature'] = _build_signature([
			payload['merchantAccount'],
			payload['merchantDomainName'],
			payload['orderReference'],
			payload['amount'],
			payload['currency'],
			payload['productName'],
			payload['productCount'],
			payload['productPrice'],
		], 'test-secret')
		response = self.client.post(reverse('main:wayforpay_callback'), payload)
		self.assertEqual(response.status_code, 200)
		self.assertFalse(Ticket.objects.filter(pk=ticket.id).exists())
		self.assertFalse(Payment.objects.filter(pk=payment.id).exists())
