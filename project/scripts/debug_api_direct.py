from django.test import RequestFactory
from main.views import api_trips, api_cities

rf = RequestFactory()
request = rf.get('/api/trips/', {'from': 'Тернопіль', 'to': 'Варшава', 'date': '2026-07-03'})
response = api_trips(request)
print('api_trips status', response.status_code)
print(response.content.decode('utf-8')[:10000])

request2 = rf.get('/api/cities/')
response2 = api_cities(request2)
print('api_cities status', response2.status_code)
print(response2.content.decode('utf-8')[:10000])
