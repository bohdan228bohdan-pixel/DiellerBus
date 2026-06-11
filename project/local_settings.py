# Local development secrets. DO NOT COMMIT this file to version control.
# Use this only for local testing. For production, set real environment variables.
import os

# LiqPay sandbox keys provided by the user (sandbox)
os.environ.setdefault('LIQPAY_PUBLIC_KEY', 'sandbox_i29733430782')
os.environ.setdefault('LIQPAY_PRIVATE_KEY', 'sandbox_WOIgiv6ZfKoBLfikeNhvlq0L6W5ZF8aiYDdSQ1tV')

# Example: set other local/test keys here if needed
# os.environ.setdefault('STRIPE_SECRET_KEY', 'sk_test_...')

# Allow enabling support admin accounts locally for development.
# Example: SUPPORT_ADMINS='dieller,ops@example.com'
os.environ.setdefault('SUPPORT_ADMINS', 'dieller')

# Development helper: run the site in local/dev mode by default when using
# this local_settings.py file. These values are safe for local testing only
# and should NOT be used in production deployments.
os.environ.setdefault('DJANGO_DEBUG', 'True')
os.environ.setdefault('ALLOWED_HOSTS', 'localhost,127.0.0.1')
os.environ.setdefault('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
