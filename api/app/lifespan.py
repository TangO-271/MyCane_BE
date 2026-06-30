import io
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger
from PIL import Image

import app.core.state as state
from app.core.db_pool import BlockingThreadedConnectionPool, DATABASE_URL
from app.core.assets import load_global_assets, _precompute_wind_variants
from app.core.render_cache import refresh_plot_render_cache
from app.core.s3 import download_from_s3, check_s3_file_exists
from app.scheduler import scheduled_cache_and_alerts

# MyCane_BE/ is 3 levels up from api/app/lifespan.py
_BE_ROOT = Path(__file__).parent.parent.parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── DB pool ──────────────────────────────────────────────────────────
    logger.info("🚀 Initializing Database Connection Pool...")
    try:
        # Keep psycopg2 + SQLAlchemy (app/core/config.py) combined under Supabase's
        # session-mode pooler limit (default 15) or new connections fail with
        # "max clients reached in session mode". maxconn=8 here + ≤5 in SQLAlchemy = 13.
        # The pool BLOCKS at maxconn (semaphore) so bursts of tile requests queue
        # instead of exceeding the limit. Override via DB_POOL_MIN / DB_POOL_MAX.
        _db_min = int(os.getenv("DB_POOL_MIN", "2"))
        _db_max = int(os.getenv("DB_POOL_MAX", "8"))
        state.db_pool = BlockingThreadedConnectionPool(minconn=_db_min, maxconn=_db_max, dsn=DATABASE_URL)
        logger.info(f"✅ Database Connection Pool initialized (min={_db_min}, max={_db_max}).")

        conn = state.db_pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_plots_geometry ON plots USING GIST (geometry);")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_plot_features_plot_timestamp "
                    "ON plot_features(plot_id, timestamp DESC);"
                )
                cur.execute("ALTER TABLE plots ADD COLUMN IF NOT EXISTS crop VARCHAR(255);")
                cur.execute("ALTER TABLE plots ADD COLUMN IF NOT EXISTS address VARCHAR(255);")
                conn.commit()
                logger.info("✅ Spatial indexes and schema columns verified.")
        finally:
            state.db_pool.putconn(conn)
    except Exception as e:
        logger.error(f"❌ Failed to initialize database: {e}")
        raise e

    # ── Empty tile ───────────────────────────────────────────────────────
    try:
        img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        state.EMPTY_TILE_BYTES = buf.getvalue()
        logger.info("✅ Empty tile bytes cached.")
    except Exception as e:
        logger.error(f"❌ Failed to cache empty tile bytes: {e}")

    # ── Static assets + render cache ─────────────────────────────────────
    logger.info("🎨 Loading static icons...")
    load_global_assets()
    _precompute_wind_variants()
    logger.info("📦 Warming plot render cache...")
    refresh_plot_render_cache()

    # ── Pre-fetch latest index TIFs from S3 (background — don't block startup) ──
    indices_dir = _BE_ROOT / "data" / "processed" / "indices" / "latest"
    indices_dir.mkdir(parents=True, exist_ok=True)

    def _prefetch_s3_tifs():
        logger.info("☁️ S3 TIF pre-fetch starting in background...")
        downloaded = False
        for layer in ["NDVI", "NDMI", "NBR"]:
            s3_key = f"processed/indices/latest_{layer}.tif"
            local_path = indices_dir / f"latest_{layer}.tif"
            if not local_path.exists() and check_s3_file_exists(s3_key):
                if download_from_s3(s3_key, str(local_path)):
                    downloaded = True
        # Tile requests arriving before the TIFs landed cached empty b"" results
        # in the raster lru_cache. Evict them so freshly-downloaded TIFs render.
        if downloaded:
            from app.services.tile_render.raster import _render_index_tile_cached
            _render_index_tile_cached.cache_clear()
            logger.info("☁️ Raster tile cache cleared after S3 pre-fetch.")
        logger.info("☁️ S3 TIF pre-fetch complete.")

    import threading
    threading.Thread(target=_prefetch_s3_tifs, daemon=True).start()
    logger.info("☁️ S3 TIF pre-fetch dispatched to background thread.")

    # ── Lightweight scheduler (alerts + cache only — no pipeline) ────────
    logger.info("🚀 Starting background scheduler (alerts + cache refresh every 30 min)...")
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        scheduled_cache_and_alerts,
        "interval",
        minutes=30,
        id="cache_and_alerts",
    )
    scheduler.start()
    logger.info("⏰ Scheduler running — cache+alerts every 30 minutes.")

    yield

    # ── Shutdown ─────────────────────────────────────────────────────────
    logger.info("🛑 Shutting down scheduler...")
    scheduler.shutdown(wait=False)

    if state.db_pool:
        logger.info("🛑 Closing Database Connection Pool...")
        state.db_pool.closeall()
        logger.info("✅ Database Connection Pool closed.")
