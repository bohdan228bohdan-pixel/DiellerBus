import hashlib
import hmac
import os
import time

import requests
from django.conf import settings


def _normalize_value(value):
    if value is None:
        return ''
    if isinstance(value, (list, tuple)):
        return ';'.join(str(v or '') for v in value)
    return str(value)


def _build_signature(fields, secret):
    secret_key = str(secret or '').encode('utf-8')
    message = ';'.join(_normalize_value(field) for field in fields).encode('utf-8')
    return hmac.new(secret_key, message, hashlib.sha1).hexdigest()


def get_wayforpay_settings():
    return {
        'merchant_login': getattr(settings, 'WAYFORPAY_MERCHANT_LOGIN', None) or os.environ.get('WAYFORPAY_MERCHANT_LOGIN', ''),
        'merchant_secret': (
            getattr(settings, 'WAYFORPAY_MERCHANT_SECRET', None)
            or getattr(settings, 'WAYFORPAY_SECRET_KEY', None)
            or os.environ.get('WAYFORPAY_MERCHANT_SECRET', '')
            or os.environ.get('WAYFORPAY_SECRET_KEY', '')
        ),
        'merchant_domain': getattr(settings, 'WAYFORPAY_DOMAIN', None) or os.environ.get('WAYFORPAY_DOMAIN', ''),
        'return_url': getattr(settings, 'WAYFORPAY_RETURN_URL', None) or os.environ.get('WAYFORPAY_RETURN_URL', ''),
        'callback_url': getattr(settings, 'WAYFORPAY_CALLBACK_URL', None) or os.environ.get('WAYFORPAY_CALLBACK_URL', ''),
        'api_url': getattr(settings, 'WAYFORPAY_API_URL', None) or os.environ.get('WAYFORPAY_API_URL', 'https://api.wayforpay.com/api'),
    }


def generate_wayforpay_signature(
    merchant_account,
    merchant_domain,
    order_reference,
    amount,
    currency,
    product_names,
    product_counts,
    product_prices,
    merchant_secret=None,
):
    """Generate WayForPay merchantSignature for invoice creation."""
    secret = merchant_secret or get_wayforpay_settings()['merchant_secret']
    names_str = _normalize_value(product_names)
    counts_str = _normalize_value(product_counts)
    prices_str = _normalize_value(product_prices)
    fields = [
        merchant_account,
        merchant_domain,
        order_reference,
        amount,
        currency,
        names_str,
        counts_str,
        prices_str,
    ]
    return _build_signature(fields, secret)


def create_wayforpay_invoice(
    order_reference,
    amount,
    currency,
    product_names,
    product_counts,
    product_prices,
    merchant_login=None,
    merchant_secret=None,
    merchant_domain=None,
    return_url=None,
    service_url=None,
    client_first_name='',
    client_last_name='',
    client_email='',
    client_phone='',
):
    """Create a WayForPay invoice through the official API endpoint."""
    cfg = get_wayforpay_settings()
    merchant_login = merchant_login or cfg['merchant_login']
    merchant_secret = merchant_secret or cfg['merchant_secret']
    merchant_domain = merchant_domain or cfg['merchant_domain'] or 'localhost'
    api_url = cfg['api_url']

    if not merchant_login or not merchant_secret:
        raise ValueError('WayForPay merchant credentials are not configured')

    payload = {
        'merchantAccount': merchant_login,
        'merchantDomainName': merchant_domain,
        'orderReference': str(order_reference),
        'orderDate': int(time.time()),
        'amount': str(amount),
        'currency': str(currency or 'UAH').upper(),
        'productName': list(product_names or [str(order_reference)]),
        'productCount': list(product_counts or ['1']),
        'productPrice': list(product_prices or [str(amount)]),
        'clientFirstName': client_first_name or '',
        'clientLastName': client_last_name or '',
        'clientEmail': client_email or '',
        'clientPhone': client_phone or '',
        'serviceUrl': service_url or cfg['callback_url'] or '',
        'returnUrl': return_url or cfg['return_url'] or '',
        'merchantAuthType': 'SimpleSignature',
        'language': 'UA',
    }
    payload['merchantSignature'] = generate_wayforpay_signature(
        payload['merchantAccount'],
        payload['merchantDomainName'],
        payload['orderReference'],
        payload['amount'],
        payload['currency'],
        payload['productName'],
        payload['productCount'],
        payload['productPrice'],
        merchant_secret=merchant_secret,
    )

    response = requests.post(api_url, json=payload, timeout=20)
    response.raise_for_status()
    try:
        return response.json()
    except Exception:
        return {'raw': response.text}


def _get_field_value(data, key):
    if hasattr(data, 'getlist'):
        values = data.getlist(key)
        if values:
            return ';'.join(str(v or '') for v in values)
    if isinstance(data, dict):
        return str(data.get(key, '') or '')
    return str(getattr(data, key, '') or '')


def verify_wayforpay_signature(signature, data):
    """Verify incoming WayForPay callback signature."""
    if not signature:
        return False
    secret = get_wayforpay_settings()['merchant_secret']
    if not secret:
        return False

    merchant_account = _get_field_value(data, 'merchantAccount')
    merchant_domain = _get_field_value(data, 'merchantDomainName')
    order_reference = _get_field_value(data, 'orderReference')
    amount = _get_field_value(data, 'amount')
    currency = _get_field_value(data, 'currency')
    product_name = _get_field_value(data, 'productName')
    product_count = _get_field_value(data, 'productCount')
    product_price = _get_field_value(data, 'productPrice')
    order_date = _get_field_value(data, 'orderDate')
    reason_code = _get_field_value(data, 'reasonCode') or _get_field_value(data, 'transactionStatus') or _get_field_value(data, 'status')
    expected = _build_signature([
        merchant_account,
        merchant_domain,
        order_reference,
        amount,
        currency,
        product_name,
        product_count,
        product_price,
    ], secret)
    if expected == str(signature or '').strip():
        return True

    fallback = _build_signature([order_reference, reason_code, order_date], secret)
    return fallback == str(signature or '').strip()


def build_wayforpay_callback_response(order_reference, status='accept', timestamp=None):
    """Build the JSON response that WayForPay expects from server-to-server callbacks."""
    if timestamp is None:
        timestamp = int(time.time())
    secret = get_wayforpay_settings()['merchant_secret']
    signature = _build_signature([str(order_reference), str(status), str(int(timestamp))], secret)
    return {
        'orderReference': str(order_reference),
        'status': status,
        'time': int(timestamp),
        'signature': signature,
    }
