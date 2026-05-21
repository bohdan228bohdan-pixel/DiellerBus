from django.conf import settings

class SecurityHeadersMiddleware:
    """Add additional security-related HTTP headers to responses.

    This middleware is intentionally conservative to avoid breaking existing
    front-end behavior. It adds headers that reduce attack surface and
    information leakage.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # Prevent MIME sniffing
        response.setdefault('X-Content-Type-Options', 'nosniff')

        # Referrer policy
        response.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')

        # Permissions policy (restrict powerful features by default)
        response.setdefault('Permissions-Policy', 'geolocation=(), microphone=(), camera=()')

        # Cross-origin opener policy — helps isolate browsing context
        response.setdefault('Cross-Origin-Opener-Policy', 'same-origin')

        # X-Frame-Options — also handled by Django's middleware, ensure value
        response.setdefault('X-Frame-Options', getattr(settings, 'X_FRAME_OPTIONS', 'DENY'))

        # HSTS only when SSL is enforced
        try:
            hsts = int(getattr(settings, 'SECURE_HSTS_SECONDS', 0) or 0)
        except Exception:
            hsts = 0
        if hsts > 0 and getattr(settings, 'SECURE_SSL_REDIRECT', False):
            response.setdefault('Strict-Transport-Security', f'max-age={hsts}; includeSubDomains; preload')

        return response
