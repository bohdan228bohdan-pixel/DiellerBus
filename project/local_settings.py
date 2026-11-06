# Local development secrets. DO NOT COMMIT this file to version control.
# Use this only for local testing. For production, set real environment variables.
import os

# WayForPay sandbox/test credentials (local development)
os.environ.setdefault('WAYFORPAY_MERCHANT_LOGIN', '127_0_0_154')
os.environ.setdefault('WAYFORPAY_MERCHANT_SECRET', '04a4c6c0dbb66765a313cc4c1d8b2ff8816a2f49')
os.environ.setdefault('WAYFORPAY_URL', 'https://secure.wayforpay.com/page')

# Example: set other local/test keys here if needed
# os.environ.setdefault('STRIPE_SECRET_KEY', 'sk_test_...')

# Allow enabling support admin accounts locally for development.
# Example: SUPPORT_ADMINS='dieller,ops@example.com'
os.environ.setdefault('SUPPORT_ADMINS', 'dieller')

# Keep DEBUG disabled by default; enable it explicitly in the environment when
# local development requires Django's detailed error pages.
os.environ.setdefault('DJANGO_DEBUG', 'False')
os.environ.setdefault('ALLOWED_HOSTS', 'localhost,127.0.0.1')
os.environ.setdefault('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
