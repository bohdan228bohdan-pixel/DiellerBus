import hashlib
import hmac
import time
from django.conf import settings


def _normalize_value(value):
    if value is None:
        return ''
    if isinstance(value, (list, tuple)):
        return ';'.join(str(v or '') for v in value)
    return str(value)


def _hmac_md5_signature(fields, secret):
    secret_key = str(secret or '').encode('utf-8')
    message = ';'.join(_normalize_value(field) for field in fields).encode('utf-8')
    return hmac.new(secret_key, message, hashlib.md5).hexdigest()


def generate_wayforpay_signature(
    merchant_account,
    merchant_domain,
    order_reference,
    amount,
    currency,
    product_names,
    product_counts,
    product_prices,
):
    """Generate WayForPay merchantSignature using HMAC-MD5."""
    secret = getattr(settings, 'WAYFORPAY_MERCHANT_SECRET', '') or ''
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
    return _hmac_md5_signature(fields, secret)


def _get_field_value(data, key):
    if hasattr(data, 'getlist'):
        values = data.getlist(key)
        if values:
            return ';'.join(str(v or '') for v in values)
    return str(data.get(key, '') or '')


def verify_wayforpay_signature(signature, data):
    """Verify incoming WayForPay callback signature."""
    if not signature:
        return False
    secret = getattr(settings, 'WAYFORPAY_MERCHANT_SECRET', '') or ''
    if not secret:
        return False
    fields = [
        _get_field_value(data, 'merchantAccount'),
        _get_field_value(data, 'merchantDomainName'),
        _get_field_value(data, 'orderReference'),
        _get_field_value(data, 'orderDate'),
        _get_field_value(data, 'amount'),
        _get_field_value(data, 'currency'),
        _get_field_value(data, 'productName'),
        _get_field_value(data, 'productCount'),
        _get_field_value(data, 'productPrice'),
    ]
    expected = _hmac_md5_signature(fields, secret)
    return expected == str(signature or '').strip()


def build_wayforpay_callback_response(order_reference, status='accept', timestamp=None):
    """Build the JSON response that WayForPay expects from server-to-server callbacks."""
    if timestamp is None:
        timestamp = int(time.time())
    signature = _hmac_md5_signature([order_reference, status, timestamp], getattr(settings, 'WAYFORPAY_MERCHANT_SECRET', '') or '')
    return {
        'orderReference': str(order_reference),
        'status': status,
        'time': int(timestamp),
        'signature': signature,
    }
