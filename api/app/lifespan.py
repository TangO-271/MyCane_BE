import io
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger
from PIL import Image

from pipeline.config import DATABASE_URL
from pipeline.utils.s3_client import download_from_s3, check_s3_file_exists
import app.core.state as state
from app.core.db_pool import BlockingThreadedConnectionPool
from app.core.assets import load_global_assets, _precompute_wind_variants
from app.core.render_cache import refresh_plot_render_cache
from app.scheduler import scheduled_startup_job

# MyCane_BE/ is 3 levels up from api/app/lifespan.py
_BE_ROOT = Path(__file__).parent.parent.parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("🚀 Initializing Database Connection Pool...")
    try:
        state.db_pool = BlockingThreadedConnectionPool(minconn=5, maxconn=40, dsn=DATABASE_URL)
        logger.info("✅ Database Connection Pool initialized successfully.")

        # Ensure spatial + covering indexes exist for high-performance spatial and LATERAL JOIN queries
        conn = state.db_pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_plots_geometry ON plots USING GIST (geometry);")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_plot_features_plot_timestamp "
                    "ON plot_features(plot_id, timestamp DESC);"
                )
                conn.commit()
                logger.info("✅ Spatial and covering indexes on plots/features verified.")
        finally:
            state.db_pool.putconn(conn)
    except Exception as e:
        logger.error(f"❌ Failed to initialize database: {e}")
        raise e

    # Pre-encode and cache transparent empty tile globally
    try:
        img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        state.EMPTY_TILE_BYTES = buf.getvalue()
        logger.info("✅ Global empty 256x256 transparent PNG bytes cached.")
    except Exception as e:
        logger.error(f"❌ Failed to cache empty tile bytes: {e}")

    # Load, crop and scale standard icons globally to eliminate disk I/O on tile requests
    logger.info("🎨 Loading and caching static PNG icons globally...")
    load_global_assets()
    _precompute_wind_variants()

    # Warm the in-memory plot render cache so the first tile request skips the DB
    logger.info("📦 Warming plot render cache...")
    refresh_plot_render_cache()

    logger.info("🚀 Initializing Background Scheduler...")
    scheduler = BackgroundScheduler()
    # Add recurring daily ingestion job at 1:00 AM Thailand time (Asia/Bangkok)
    scheduler.add_job(scheduled_startup_job, 'cron', hour=1, minute=0, timezone='Asia/Bangkok', id='daily_ingestion')
    # Schedule an initial run 10 seconds after startup so it doesn't block FastAPI booting
    scheduler.add_job(scheduled_startup_job, 'date', run_date=datetime.now() + timedelta(seconds=10), id='initial_ingestion')
    scheduler.start()
    logger.info("⏰ Scheduler is now running. Daily ingestion job scheduled at 01:00 AM Thailand time. Startup run in 10 seconds.")

    # Pre-fetch latest TIFs from S3 on startup to warm up cache
    logger.info("☁️ Pre-fetching latest satellite indices from S3...")
    indices_dir = _BE_ROOT / "data" / "processed" / "indices" / "latest"
    indices_dir.mkdir(parents=True, exist_ok=True)

    for layer in ["NDVI", "NDMI", "NBR"]:
        s3_key = f"processed/indices/latest_{layer}.tif"
        local_path = indices_dir / f"latest_{layer}.tif"
        if not local_path.exists() and check_s3_file_exists(s3_key):
            download_from_s3(s3_key, str(local_path))

    yield

    # Shutdown
    logger.info("🛑 Shutting down scheduler...")
    scheduler.shutdown(wait=False)

    if state.db_pool:
        logger.info("🛑 Closing Database Connection Pool...")
        state.db_pool.closeall()
        logger.info("✅ Database Connection Pool closed.")
