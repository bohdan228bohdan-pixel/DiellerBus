from django.test import Client
c = Client()
res = c.get('/api/trips/', {'from':'Копачівка','to':'Пулави','date':'2026-06-25','pax':'1','currency':'UAH'})
print(res.status_code)
print(res.content.decode('utf-8'))
