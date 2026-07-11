from django.test import Client

c = Client()
resp = c.get('/api/trips/', {'from': 'Тернопіль', 'to': 'Варшава', 'date': '2026-07-03'})
print('status', resp.status_code)
try:
    data = resp.json()
    print('trips:', len(data.get('trips', [])))
    for t in data.get('trips', []):
        print('--- trip ---')
        for k, v in t.items():
            if k == 'stops':
                print('stops count', len(v))
                for s in v:
                    print('   ', s)
            else:
                print(k, v)
except Exception as e:
    print('json error', e)
    print(resp.content)
