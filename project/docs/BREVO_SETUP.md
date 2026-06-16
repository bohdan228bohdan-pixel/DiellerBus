# Налаштування Brevo (Sendinblue) для відправки пошти

Це коротка інструкція як підключити Brevo (SMTP) для відправки листів з цього Django-проєкту.

1) Обрати метод

- SMTP (швидко, працює з поточними налаштуваннями `EMAIL_BACKEND`).
- API (через офіційний SDK або Anymail) — дає додаткові можливості, але потребує бібліотеки і додаткової реалізації.

Я реалізував швидкий і безпечний варіант — використання SMTP. Щоб це працювало в продакшні (Render):

2) Додати (на Render — через Dashboard → ENV / Environment variables) наступні змінні оточення:

```
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp-relay.sendinblue.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=apikey
EMAIL_HOST_PASSWORD=<ВАШ_BREVO_SMTP_API_KEY>
DEFAULT_FROM_EMAIL=Dieller Bus <noreply@diellerbus.com>
```

Пояснення:
- `EMAIL_HOST_USER` для Brevo/Sendinblue при SMTP зазвичай ставиться як `apikey`, а пароль — ваш SMTP ключ (API key).
- Не зберігайте ключі у репозиторії — тільки в ENV на Render.

3) Перевірка локально перед деплоєм

Експортні змінні локально (PowerShell):

```powershell
$env:DJANGO_DEBUG='False'
$env:EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend'
$env:EMAIL_HOST='smtp-relay.sendinblue.com'
$env:EMAIL_PORT='587'
$env:EMAIL_USE_TLS='True'
$env:EMAIL_HOST_USER='apikey'
$env:EMAIL_HOST_PASSWORD='<ВАШ_BREVO_SMTP_API_KEY>'
$env:DEFAULT_FROM_EMAIL='Dieller Bus <noreply@diellerbus.com>'
python manage.py send_test_email --to you@example.com
```

4) Тест на сервері / після деплою

- Я додав management-команду `send_test_email` у `main`.
- На Render (після того як ви додали ENV) можна виконати команду через Shell або виконати `python manage.py send_test_email --to you@example.com` щоб відправити тестовий лист.

5) Додаткові рекомендації

- Якщо хочете використовувати API (REST) замість SMTP — скажіть і я додам приклад з `brevo` SDK або `django-anymail`.
- Переконайтесь, що `DEFAULT_FROM_EMAIL` відповідає підтвердженому відправнику у вашому Brevo акаунті або що у Brevo налаштовано дозволений домен/адресу.

Якщо хочете — я можу поставити ці ENV (ви даєте доступ до Render) або підкажу як це зробити крок за кроком.
