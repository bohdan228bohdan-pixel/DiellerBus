from django.apps import AppConfig


class MainConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'main'
    
    def ready(self):
        # Import signal handlers to ensure they're registered
        try:
            import main.signals  # noqa: F401
        except Exception:
            pass
