"""
ASGI config for buswebsite project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'buswebsite.settings')

# Default Django ASGI app
django_asgi_app = get_asgi_application()

# If Channels is available, mount WebSocket handler; otherwise expose default ASGI app
try:
	from channels.routing import ProtocolTypeRouter, URLRouter
	from channels.auth import AuthMiddlewareStack
	import main.routing as main_routing

	application = ProtocolTypeRouter({
		"http": django_asgi_app,
		"websocket": AuthMiddlewareStack(
			URLRouter(
				main_routing.websocket_urlpatterns
			)
		),
	})
except Exception:
	application = django_asgi_app
