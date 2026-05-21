# Local development secrets. DO NOT COMMIT this file to version control.
# Use this only for local testing. For production, set real environment variables.
import os

# LiqPay sandbox keys provided by the user (sandbox)
os.environ.setdefault('LIQPAY_PUBLIC_KEY', 'sandbox_i29733430782')
os.environ.setdefault('LIQPAY_PRIVATE_KEY', 'sandbox_WOIgiv6ZfKoBLfikeNhvlq0L6W5ZF8aiYDdSQ1tV')

# Example: set other local/test keys here if needed
# os.environ.setdefault('STRIPE_SECRET_KEY', 'sk_test_...')
