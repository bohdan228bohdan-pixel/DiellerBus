# HostIQ / cPanel — налаштування SMTP для noreply@diellerbus.com

Це коротка інструкція що робити в cPanel (HostIQ) і які записи додати в DNS, щоб `noreply@diellerbus.com` коректно відправляв листи.

1) Зміна пароля поштової скриньки
- Увійдіть в cPanel → Email Accounts → знайдіть `noreply@diellerbus.com` → Manage → Security → Set Password. Збережіть пароль у безпечному сховищі.

2) Де знайти SMTP / порти
- У cPanel → Email Accounts → біля скриньки натисніть **Connect Devices** або **Configure Mail Client** — там є готові параметри для IMAP/POP/SMTP, включаючи хости і порти. Для вашого акаунта це, судячи з панелі: `uashared27.twinservers.net`, SMTP port 465 (SSL).

3) SPF (TXT)
- Краще використати рекомендований рядок з cPanel → **Email Deliverability** (або **Authentication**). Натисніть для домену `diellerbus.com` → cPanel запропонує готовий SPF TXT — скопіюйте і додайте в DNS.
- Якщо потрібно тимчасово: використайте мінімальний, який дозволяє MX відправляти пошту:

```
v=spf1 mx ~all
```

4) DKIM
- У **Email Deliverability** увімкніть DKIM (Install / Repair). cPanel згенерує DKIM TXT (зазвичай під ім'ям `default._domainkey` або `selector._domainkey`) — скопіюйте повний запис і додайте в DNS як TXT.

5) DMARC (рекомендований тестовий запис)
- Додайте TXT запис для `_dmarc.diellerbus.com` з початковою політикою `none` для моніторингу:

```
_dmarc  TXT  "v=DMARC1; p=none; rua=mailto:postmaster@diellerbus.com; ruf=mailto:postmaster@diellerbus.com; pct=100"
```

Після тестування можна змінити `p=none` → `p=quarantine` або `p=reject`.

6) PTR / Reverse DNS
- PTR (reverse DNS) для IP адреси контролює провайдер хостингу (HostIQ). Якщо ви розгортаєте на виділеному IP або ваш провайдер відправляє мейли через їхні IP — попросіть HostIQ встановити PTR, що відповідає вашому доменному імені (наприклад `uashared27.twinservers.net` або `mail.diellerbus.com`). Для shared-hosting PTR зазвичай встановлений на ім'я провайдера і змінити його часто не можна.

7) Перевірка
- Після внесення записів зачекайте пропагацію (до 48 годин, зазвичай швидше). Перевірити можна через https://mxtoolbox.com або https://dnschecker.org (SPF/DKIM/DMARC).

8) ENV для Django (Render або HostIQ app)
- Додайте такі змінні оточення в панелі де розгорнуто сайт (Render Dashboard або HostIQ app env):

```
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=uashared27.twinservers.net
EMAIL_PORT=465
EMAIL_USE_SSL=True
EMAIL_USE_TLS=False
EMAIL_HOST_USER=noreply@diellerbus.com
EMAIL_HOST_PASSWORD=<PASSWORD_FOR_NOREPLY>
DEFAULT_FROM_EMAIL=Dieller Bus <noreply@diellerbus.com>
DJANGO_DEBUG=False
```

9) Тест відправки
- На сервері (або через Render shell) виконайте:

```bash
python manage.py collectstatic --noinput
python manage.py send_test_email --to your.email@domain.com
```

Перевірте логи: Render Logs або `logs/django.log` в проекті. Якщо отримуєте помилку автентифікації — перевірте `EMAIL_HOST_USER` і `EMAIL_HOST_PASSWORD`.

10) Поради для кращої доставлюваності
- Увімкніть DKIM і додайте коректний SPF (не ставте `-all` відразу). Налаштуйте PTR якщо у вас виділений IP. Перевіряйте заголовки отриманих листів (мають бути DKIM-підпис, SPF pass).

---
Якщо хочеш — можу згенерувати точні DNS-записи, якщо ти надішлеш скопійовані рядки із cPanel → Email Deliverability (SPF і DKIM). Не надсилай паролі у чаті — тільки TXT-рядки і host/port.
