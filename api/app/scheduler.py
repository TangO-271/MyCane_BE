"""Lightweight BE scheduler — alert scanning + render cache refresh only.
Pipeline ingestion (run_poc.py) now runs independently in MyCane_DB via
GitHub Actions or its own worker; this scheduler no longer calls run_pipeline()."""
from loguru import logger
from app.services.alert_engine import run_alert_scan
from app.services.notification_cleanup import purge_stale_notifications
from app.core.render_cache import refresh_plot_render_cache


def scheduled_cache_and_alerts():
    """Refresh the in-memory plot render cache, raise any new hotspot alerts, and purge
    stale notifications. Called every 30 minutes so the BE picks up fresh pipeline output
    promptly and old/resolved alerts don't pile up."""
    try:
        result = run_alert_scan()
        logger.info(f"🔔 Alert scan: {result}")
    except Exception as e:
        logger.error(f"❌ Alert scan failed: {e}")

    try:
        purge = purge_stale_notifications()
        logger.info(f"🧹 Notification purge: {purge}")
    except Exception as e:
        logger.error(f"❌ Notification purge failed: {e}")

    try:
        refresh_plot_render_cache()
    except Exception as e:
        logger.error(f"❌ Render cache refresh failed: {e}")
