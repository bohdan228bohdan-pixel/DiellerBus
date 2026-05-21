from .models import SiteConfig


def site_config(request):
    """Inject site configuration into templates as `site_config`."""
    try:
        cfg = SiteConfig.get_solo()
    except Exception:
        cfg = None
    return {'site_config': cfg}
