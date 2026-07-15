import hashlib
import hmac
import json
import logging
import os
import time

import requests
from django.conf import settings


class WayForPayService:
    """Single service for WayForPay invoice creation, signature verification, and callback responses."""

    def __init__(self, merchant_login=None, merchant_secret=None, merchant_domain=None, return_url=None, callback_url=None, api_url=None):
        self.merchant_login = merchant_login
        self.merchant_secret = merchant_secret
        self.merchant_domain = merchant_domain
        self.return_url = return_url
        self.callback_url = callback_url
        self.api_url = api_url

    @staticmethod
    def _normalize_value(value):
        if value is None:
            return ''
        if isinstance(value, (list, tuple)):
            return ';'.join(str(v or '') for v in value)
        return str(value)

    @classmethod
    def _build_signature(cls, fields, secret):
        message = ';'.join(cls._normalize_value(field) for field in fields)
        if secret:
            return hmac.new(secret.encode('utf-8'), message.encode('utf-8'), hashlib.md5).hexdigest()
        return hashlib.md5(message.encode('utf-8')).hexdigest()

    @classmethod
    def _build_signature_for_callback(cls, fields, secret):
        return cls._build_signature(fields, secret)

    def build_signature(
        self,
        merchant_account,
        merchant_domain,
        order_reference,
        amount,
        currency,
        product_names,
        product_counts,
        product_prices,
        merchant_secret=None,
        order_date=None,
    ):
        """Generate a WayForPay merchantSignature for invoice creation."""
        secret = merchant_secret or self.merchant_secret
        names_str = self._normalize_value(product_names)
        counts_str = self._normalize_value(product_counts)
        prices_str = self._normalize_value(product_prices)
        fields = [
            merchant_account,
            merchant_domain,
            order_reference,
            order_date or '',
            amount,
            currency,
            names_str,
            counts_str,
            prices_str,
        ]
        return self._build_signature(fields, secret)

    def build_invoice_payload(
        self,
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
        merchant_login = merchant_login or self.merchant_login
        merchant_secret = merchant_secret or self.merchant_secret
        merchant_domain = merchant_domain or self.merchant_domain or 'localhost'
        if not merchant_login or not merchant_secret:
            raise ValueError('WayForPay merchant credentials are not configured')

        # normalize product lists and amounts to match WayForPay expectations
        product_names_list = [str(n) for n in (product_names or [str(order_reference)])]
        product_counts_list = [str(int(c)) for c in (product_counts or ['1'])]
        # prices must use dot and two decimal places (e.g. 10 -> 10.00)
        product_prices_list = ["{:.2f}".format(float(p)) for p in (product_prices or [amount])]
        payload = {
            'merchantAccount': merchant_login,
            'merchantDomainName': merchant_domain,
            'orderReference': str(order_reference),
            'orderDate': int(time.time()),
            'amount': "{:.2f}".format(float(amount)),
            'currency': str(currency or 'UAH').upper(),
            'productName': product_names_list,
            'productCount': product_counts_list,
            'productPrice': product_prices_list,
            'clientFirstName': client_first_name or 'Customer',
            'clientLastName': client_last_name or 'Customer',
            'clientEmail': client_email or 'customer@example.com',
            'clientPhone': client_phone or '+380000000000',
            'serviceUrl': service_url or self.callback_url or '',
            'returnUrl': return_url or self.return_url or '',
            'merchantAuthType': 'SimpleSignature',
            'language': 'UA',
            'apiVersion': '1',
            'transactionType': 'CREATE_INVOICE',
        }
        payload['merchantSignature'] = self.build_signature(
            payload['merchantAccount'],
            payload['merchantDomainName'],
            payload['orderReference'],
            payload['amount'],
            payload['currency'],
            payload['productName'],
            payload['productCount'],
            payload['productPrice'],
            merchant_secret=merchant_secret,
            order_date=payload['orderDate'],
        )
        return payload

    def create_invoice(self, *args, **kwargs):
        payload = self.build_invoice_payload(*args, **kwargs)
        api_url = self.api_url or get_wayforpay_settings()['api_url']
        try:
            response = requests.post(api_url, json=payload, timeout=20)
            try:
                data = response.json()
            except ValueError:
                response_text = getattr(response, 'text', None)
                if response_text is None and hasattr(response, 'content'):
                    response_text = response.content.decode('utf-8', errors='replace') if isinstance(response.content, (bytes, bytearray)) else str(response.content)
                data = {'raw': response_text}

            logger = logging.getLogger('main')
            logger.debug('WayForPay request payload=%s', json.dumps(payload, ensure_ascii=False))
            response_text = getattr(response, 'text', None)
            if response_text is None and hasattr(response, 'content'):
                response_text = response.content.decode('utf-8', errors='replace') if isinstance(response.content, (bytes, bytearray)) else str(response.content)
            logger.debug('WayForPay response status=%s body=%s', response.status_code, response_text)

            if response.status_code >= 400 or not data:
                logger = logging.getLogger('main')
                logger.exception('WayForPay API request failed: status=%s payload=%s response=%s', response.status_code, payload, response_text)
                return {
                    'error': 'WayForPay API request failed',
                    'status_code': response.status_code,
                    'response_text': response_text,
                    'response_json': data,
                    'payload': payload,
                }
            return data
        except Exception as exc:
            logger = logging.getLogger('main')
            logger.exception('WayForPay API exception: %s', exc)
            return {
                'error': 'WayForPay API request failed',
                'exception': str(exc),
                'payload': payload,
            }

    @staticmethod
    def _get_field_value(data, key):
        if hasattr(data, 'getlist'):
            values = data.getlist(key)
            if values:
                return ';'.join(str(v or '') for v in values)
        if isinstance(data, dict):
            return str(data.get(key, '') or '')
        return str(getattr(data, key, '') or '')

    def verify_signature(self, signature, data):
        """Verify a WayForPay signature coming from the gateway or callback."""
        if not signature:
            return False
        secret = self.merchant_secret or get_wayforpay_settings()['merchant_secret']
        if not secret:
            return False

        merchant_account = self._get_field_value(data, 'merchantAccount')
        merchant_domain = self._get_field_value(data, 'merchantDomainName')
        order_reference = self._get_field_value(data, 'orderReference')
        amount = self._get_field_value(data, 'amount')
        currency = self._get_field_value(data, 'currency')
        product_name = self._get_field_value(data, 'productName')
        product_count = self._get_field_value(data, 'productCount')
        product_price = self._get_field_value(data, 'productPrice')
        reason_code = self._get_field_value(data, 'reasonCode') or self._get_field_value(data, 'transactionStatus') or self._get_field_value(data, 'status')
        expected = self._build_signature([
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

        status = self._get_field_value(data, 'transactionStatus') or self._get_field_value(data, 'status')
        time_value = self._get_field_value(data, 'time')
        if status and time_value:
            callback_expected = self._build_signature([order_reference, status, time_value], secret)
            if callback_expected == str(signature or '').strip():
                return True

        if reason_code:
            fallback = self._build_signature([order_reference, reason_code], secret)
            if fallback == str(signature or '').strip():
                return True

        return False

    def build_callback_response(self, order_reference, status='accept', timestamp=None):
        """Build the JSON response that WayForPay expects from server-to-server callbacks."""
        if timestamp is None:
            timestamp = int(time.time())
        secret = self.merchant_secret or get_wayforpay_settings()['merchant_secret']
        signature = self._build_signature([str(order_reference), str(status), str(int(timestamp))], secret)
        return {
            'orderReference': str(order_reference),
            'status': status,
            'time': int(timestamp),
            'signature': signature,
        }


def _normalize_value(value):
    return WayForPayService._normalize_value(value)


def _build_signature(fields, secret):
    return WayForPayService._build_signature(fields, secret)


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


def get_wayforpay_service(**overrides):
    cfg = get_wayforpay_settings()
    return WayForPayService(
        merchant_login=overrides.get('merchant_login', cfg['merchant_login']),
        merchant_secret=overrides.get('merchant_secret', cfg['merchant_secret']),
        merchant_domain=overrides.get('merchant_domain', cfg['merchant_domain']),
        return_url=overrides.get('return_url', cfg['return_url']),
        callback_url=overrides.get('callback_url', cfg['callback_url']),
        api_url=overrides.get('api_url', cfg['api_url']),
    )


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
    service = get_wayforpay_service(merchant_secret=merchant_secret)
    return service.build_signature(
        merchant_account,
        merchant_domain,
        order_reference,
        amount,
        currency,
        product_names,
        product_counts,
        product_prices,
        merchant_secret=merchant_secret,
    )


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
    service = get_wayforpay_service(
        merchant_login=merchant_login,
        merchant_secret=merchant_secret,
        merchant_domain=merchant_domain,
        return_url=return_url,
        callback_url=service_url,
    )
    return service.create_invoice(
        order_reference=order_reference,
        amount=amount,
        currency=currency,
        product_names=product_names,
        product_counts=product_counts,
        product_prices=product_prices,
        merchant_login=merchant_login,
        merchant_secret=merchant_secret,
        merchant_domain=merchant_domain,
        return_url=return_url,
        service_url=service_url,
        client_first_name=client_first_name,
        client_last_name=client_last_name,
        client_email=client_email,
        client_phone=client_phone,
    )


def verify_wayforpay_signature(signature, data):
    """Verify incoming WayForPay callback signature."""
    return get_wayforpay_service().verify_signature(signature, data)


def build_wayforpay_callback_response(order_reference, status='accept', timestamp=None):
    """Build the JSON response that WayForPay expects from server-to-server callbacks."""
    return get_wayforpay_service().build_callback_response(order_reference, status=status, timestamp=timestamp)
