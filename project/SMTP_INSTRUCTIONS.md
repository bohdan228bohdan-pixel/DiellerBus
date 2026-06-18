SMTP setup and testing
======================

Short version
-------------
- Configure the following environment variables on your server/hosting provider (Render, Heroku, etc.):
  - `EMAIL_BACKEND` (optional) — default: `django.core.mail.backends.smtp.EmailBackend`
  - `EMAIL_HOST` — SMTP host (eg. `smtp.gmail.com`, `smtp.sendgrid.net`)
  - `EMAIL_PORT` — usually `587` for TLS, `465` for SSL
  - `EMAIL_USE_TLS` — `True` or `False` (TLS on port 587)
  - `EMAIL_HOST_USER` — SMTP username (email or API user)
  - `EMAIL_HOST_PASSWORD` — SMTP password or API key (DO NOT commit this to VCS)
  - `DEFAULT_FROM_EMAIL` — sender address shown in outgoing mails (eg. `Dieller Bus <no-reply@yourdomain.com>`)

Detailed notes
--------------
- The project already reads these values from environment variables in `buswebsite/settings.py`.
- For local development, you can use the console backend to avoid sending real mail:

```python
EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend'
```

- If you use Gmail (personal or Workspace):
  - For regular Gmail accounts you'll need to create an App Password (Account -> Security -> App passwords) and use it as `EMAIL_HOST_PASSWORD`.
  - Alternatively use SMTP relay or a transactional email provider (SendGrid, Mailgun, Amazon SES).

- For Render.com: open your service -> Environment -> Add Environment Variables. Add the keys above and redeploy.

Testing on the server
---------------------
1. Ensure environment variables are set on the server and the app is redeployed/restarted so Django picks them up.
2. Run the management command on the server to send a test email:

```bash
python manage.py send_test_email you@yourdomain.com --subject "Test" --message "Hello from Dieller Bus"
```

3. Check the command output — if successful you'll see "Test email sent to ...". If it fails, the error will explain the reason (auth, connection, TLS).

Security
--------
- Never put real SMTP credentials into the repository. Use the hosting provider's secrets/vars interface.
- If using Gmail, prefer an App Password and enable 2FA on the account.

If you want, I can:
- create a short PR that updates any deployment README with these env var names (done),
- or prepare a small script to rotate a test message and report success into logs.

Render-specific notes
---------------------
- Recommended SMTP host for Brevo: `smtp-relay.brevo.com` (port `587`, TLS `True`).
- On Render: open your service → Environment → Add Environment Variables and set:

```
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp-relay.brevo.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=apikey
EMAIL_HOST_PASSWORD=<YOUR_BREVO_SMTP_KEY>
DEFAULT_FROM_EMAIL=Dieller Bus <noreply@yourdomain.com>
```

- After adding env vars, deploy/restart the service. Test with the management command:

```bash
python manage.py send_test_email --to you@example.com --subject "Render Brevo test" --message "Hello from Render"
```

If the command prints a success message, incoming SMTP logs in Brevo will also show activity. If sending fails, check Render logs and verify the SMTP key and host values.
