import sqlite3
import os

DB = os.path.join(os.path.dirname(__file__), '..', 'project', 'db.sqlite3')
DB = os.path.normpath(DB)
print('DB:', DB)
con = sqlite3.connect(DB)
cur = con.cursor()
print('\nTrips:')
for row in cur.execute('SELECT id, route_id, title, start_city_id, end_city_id, direction, base_price, seats FROM main_trip'):
    print(row)
print('\nTripStops:')
for row in cur.execute('SELECT id, trip_id, city_id, [order], arrival_time, departure_time, address FROM main_tripstop ORDER BY trip_id, [order]'):
    print(row)
print('\nCities:')
for row in cur.execute('SELECT id, name, country FROM main_city'):
    print(row)
con.close()
