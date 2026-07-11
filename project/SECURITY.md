# SECURITY CHECKLIST & INCIDENT RESPONSE

This file contains practical guidance and quick actions to improve security and recover from incidents.

Immediate hardening applied by the codebase:
- Payment payloads are scrubbed before being saved (no PAN/CVV kept).
- Admin UI no longer prints raw password hashes or revealed passwords.
- Security headers middleware adds `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy` and (when enabled) `Strict-Transport-Security`.
- Settings are now environment-driven for secrets (use `DJANGO_SECRET_KEY`, `EMAIL_HOST_PASSWORD`, etc.).
- Rotating file logging added to `logs/django.log` for audits.

Recommended production checklist (do these now):

1) ENVIRONMENT / SECRETS
- Set `DJANGO_SECRET_KEY` to a strong random value and remove the fallback in source control.
- Do not commit any secrets (email passwords, API keys). Use environment variables or a secrets manager (HashiCorp Vault, AWS Secrets Manager, Azure Key Vault).
- Rotate WayForPay keys if they were exposed.

2) HTTPS / HSTS
- Enable TLS and set `SECURE_SSL_REDIRECT=True`, `SECURE_HSTS_SECONDS` (e.g. 31536000), `SECURE_HSTS_INCLUDE_SUBDOMAINS=True`, `SECURE_HSTS_PRELOAD=True` in the environment.
- Configure your reverse proxy (nginx, cloud load balancer) to terminate TLS and forward secure headers.

3) COOKIES & CSRF
- In production set `SESSION_COOKIE_SECURE=True` and `CSRF_COOKIE_SECURE=True`.
- Keep `SESSION_COOKIE_HTTPONLY=True` (already enabled).

4) PAYMENTS / PCI
- Never store raw card PAN/CVV. Use PCI-compliant processors (WayForPay) and rely on their tokens.
- If you must store payment metadata, strip any fields that may contain sensitive card data.

5) ADMIN & ACCESS
- Restrict `ALLOWED_HOSTS` and admin access (IP firewall, or admin path behind VPN).
- Use strong, unique admin passwords and enable 2FA for admin accounts.
- Create separate low-privilege 'support' accounts for daily tasks; do not give them staff/superuser rights.
- Audit and remove unused staff accounts.

6) BACKUPS & RECOVERY
- Regularly export and store encrypted backups off-server.
- Use the included `scripts/backup_db.py` to snapshot the SQLite DB; move backups to secure off-host storage.
- Maintain backup retention policy and test restores regularly.

7) LOGGING & MONITORING
- Monitor `logs/django.log` and set up log shipping to a secure central log service.
- Alert on suspicious activity (repeated login failures, unusual admin changes, large export requests).

8) INCIDENT RESPONSE (quick steps)
- If compromise suspected: rotate `DJANGO_SECRET_KEY`, database credentials, payment processor keys, email passwords.
- Revoke active sessions (change session signing keys or expire sessions table if used).
- Restore from known-good backup if necessary.
- Preserve forensic logs (do not overwrite) and consider taking disk images.

If you want, I can:
- Add a script to rotate and expire sessions when a secret is rotated.
- Integrate an audit trail model for critical actions (ticket refunds, manual payments, user creation).
- Configure fail2ban or similar rate-limiting at webserver level.


# Where to start right now (recommended):
1) Set env vars: `DJANGO_SECRET_KEY`, `EMAIL_HOST_PASSWORD`, `ALLOWED_HOSTS`.
2) Enable `SESSION_COOKIE_SECURE` and `CSRF_COOKIE_SECURE` in production.
3) Rotate any possibly leaked keys (WayForPay, email SMTP password).
4) Run `python scripts/backup_db.py` to create a snapshot and move it to secure storage.

