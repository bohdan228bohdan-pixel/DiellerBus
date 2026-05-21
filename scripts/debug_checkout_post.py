from django.test import Client
from django.contrib.auth.models import User
from main.models import Ticket

c = Client()
if not User.objects.filter(username='testbuyer').exists():
    User.objects.create_user('testbuyer', 'testbuyer@example.com', 'testpass', is_active=True)

logged = c.login(username='testbuyer', password='testpass')
print('logged', logged)

res = c.post('/checkout/1/', {'passengers':'1','passenger_first':'A','passenger_last':'B','email':'testbuyer@example.com','phone':'380661234567','date':'2026-05-10'})
print('status', res.status_code)
text = res.content.decode(errors='ignore')[:4000]
print('liqpayForm_in_text', 'liqpayForm' in text)
print(text)
print('tickets count', Ticket.objects.count())
if Ticket.objects.exists():
    t = Ticket.objects.order_by('-created_at').first()
    print('last ticket', t.id, t.travel_date, t.total_price, t.paid)
    print('payments count', t.payments.count())
