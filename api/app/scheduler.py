from loguru import logger
from run_poc import main as run_pipeline
from app.services.alert_engine import run_alert_scan
from app.core.render_cache import refresh_plot_render_cache


def scheduled_phase1_job():
    logger.info("🔄 Starting Phase 1 Sentinel-2 tile pre-computation...")
    try:
        run_pipeline(phase1=True, phase2=False)
        logger.info("✅ Phase 1 pre-computation completed successfully.")
    except Exception as e:
        logger.error(f"❌ Phase 1 pipeline failed: {str(e)}")


def scheduled_phase2_job():
    logger.info("🔄 Starting Phase 2 plot-by-plot data ingestion pipeline...")
    try:
        run_pipeline(phase1=False, phase2=True)
        logger.info("✅ Phase 2 pipeline completed successfully.")
    except Exception as e:
        logger.error(f"❌ Phase 2 pipeline failed: {str(e)}")

    try:
        result = run_alert_scan()
        logger.info(f"🔔 Alert scan: {result}")
    except Exception as e:
        logger.error(f"❌ Alert scan failed: {str(e)}")

    try:
        refresh_plot_render_cache()
    except Exception as e:
        logger.error(f"❌ Plot render cache refresh failed: {str(e)}")


def scheduled_startup_job():
    logger.info("🔄 Starting startup ingestion pipeline (Phase 1 & Phase 2)...")
    try:
        run_pipeline(phase1=True, phase2=True)
        logger.info("✅ Startup ingestion pipeline completed successfully.")
    except Exception as e:
        logger.error(f"❌ Startup pipeline failed: {str(e)}")

    try:
        result = run_alert_scan()
        logger.info(f"🔔 Alert scan: {result}")
    except Exception as e:
        logger.error(f"❌ Alert scan failed: {str(e)}")

    # Refresh in-memory plot render cache so tile rendering reflects the latest risk scores
    try:
        refresh_plot_render_cache()
    except Exception as e:
        logger.error(f"❌ Plot render cache refresh failed: {str(e)}")


def scheduled_job():
    scheduled_startup_job()
