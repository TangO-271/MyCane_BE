"""Lightweight BE scheduler — alert scanning + render cache refresh only.
Pipeline ingestion (run_poc.py) now runs independently in MyCane_DB via
GitHub Actions or its own worker; this scheduler no longer calls run_pipeline()."""
from loguru import logger
from app.services.alert_engine import run_alert_scan
from app.core.render_cache import refresh_plot_render_cache


def scheduled_cache_and_alerts():
    """Refresh the in-memory plot render cache and raise any new hotspot alerts.
    Called every 30 minutes so the BE picks up fresh pipeline output promptly."""
    try:
        result = run_alert_scan()
        logger.info(f"🔔 Alert scan: {result}")
    except Exception as e:
        logger.error(f"❌ Alert scan failed: {e}")

    try:
        refresh_plot_render_cache()
    except Exception as e:
        logger.error(f"❌ Render cache refresh failed: {e}")
