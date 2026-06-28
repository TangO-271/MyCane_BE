from fastapi import FastAPI, HTTPException, Depends, Query, Response, Request
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Literal, Sequence
from datetime import datetime, timedelta
import psycopg2
from psycopg2 import pool
import functools
import json
import sys
import io
import math
from pathlib import Path
import mercantile
import rasterio
from rasterio.warp import reproject, Resampling
import numpy as np
from PIL import Image, ImageDraw
from shapely.wkt import loads as wkt_loads

from contextlib import asynccontextmanager
from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger
import threading
from psycopg2.pool import ThreadedConnectionPool

class BlockingThreadedConnectionPool(ThreadedConnectionPool):
    def __init__(self, minconn, maxconn, *args, **kwargs):
        super().__init__(minconn, maxconn, *args, **kwargs)
        self._semaphore = threading.Semaphore(maxconn)

    def getconn(self, key=None):
        self._semaphore.acquire()
        try:
            return super().getconn(key)
        except Exception:
            self._semaphore.release()
            raise

    def putconn(self, conn, key=None, close=False):
        try:
            super().putconn(conn, key, close)
        finally:
            self._semaphore.release()

# Add project root and api directory to path for imports
sys.path.append(str(Path(__file__).parent.parent))
sys.path.append(str(Path(__file__).parent))
from pipeline.config import DATABASE_URL

# Thread-safe database connection pool for microsecond-level connection reuse
db_pool = None

# Global static asset cache for ultra-low latency tile rendering
GLOBAL_ICONS = {
    "fire": None,
    "wind": None,
    "disease": None,
    "water": None,
    "cyclone": None
}
EMPTY_TILE_BYTES = None

# Pre-computed wind icon variants: (px_size, angle_slot) → PIL.Image
# Eliminates per-hotspot LANCZOS resize + BICUBIC rotate during tile rendering.
# Sizes: 26–42px (even steps), Angles: 16 slots × 22.5° each (0°–337.5°).
WIND_ICON_VARIANTS: dict[tuple, "Image.Image"] = {}

# In-process vector tile cache — keyed by ETag string (includes layer+coords+hour boundary).
# Cleared whenever the hour rolls over so stale tiles never survive past the hourly data update.
_vector_tile_cache: dict[str, bytes] = {}
_vtile_lock = threading.Lock()
_vtile_cache_hour: str = ""

# In-memory plot render cache — rebuilt after each scheduled sync (hourly) and at startup.
# Stores pre-fetched risk scores, feature data, and geometry bbox per plot so vector tile
# rendering skips DB queries entirely on cache hits.
_plot_render_cache: dict[int, dict] = {}
_plot_render_cache_lock = threading.Lock()
# Cached global wind fallback (latest wind reading across all plots).
_global_wind_cache: dict = {"deg": 225.0, "speed": 12.0}

from app.api.auth import router as auth_router
from app.api.plots import router as plots_router
from app.api.notifications import router as notifications_router
from app.services.alert_engine import run_alert_scan
from run_poc import main as run_pipeline
from pipeline.utils.s3_client import download_from_s3, check_s3_file_exists

db_pool = None

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

def load_global_assets():
    global GLOBAL_ICONS
    import os
    
    # 1. Fire Icon
    try:
        icon_paths = [
            str(Path(__file__).parent / "static" / "fire_icon.png"),
        ]
        for path in icon_paths:
            if os.path.exists(path):
                raw_icon = Image.open(path).convert("RGBA")
                bbox = raw_icon.getbbox()
                if bbox:
                    raw_icon = raw_icon.crop(bbox)
                w, h = raw_icon.size
                target_h = 36
                target_w = max(4, int(target_h * (w / h)))
                GLOBAL_ICONS["fire"] = raw_icon.resize((target_w, target_h), Image.LANCZOS)
                logger.info(f"✅ Cached fire icon from {path}")
                break
    except Exception as e:
        logger.error(f"Error caching fire icon: {e}")

    # 2. Wind Icon
    try:
        w_icon_paths = [
            str(Path(__file__).parent / "static" / "wind_icon.png"),
        ]
        for path in w_icon_paths:
            if os.path.exists(path):
                raw_w_icon = Image.open(path).convert("RGBA")
                w_bbox = raw_w_icon.getbbox()
                if w_bbox:
                    raw_w_icon = raw_w_icon.crop(w_bbox)
                GLOBAL_ICONS["wind"] = raw_w_icon
                logger.info(f"✅ Cached wind icon from {path}")
                break
    except Exception as e:
        logger.error(f"Error caching wind icon: {e}")

    # 3. Disease Icon
    try:
        icon_paths = [
            str(Path(__file__).parent / "static" / "disease_icon.png"),
        ]
        for path in icon_paths:
            if os.path.exists(path):
                raw_icon = Image.open(path).convert("RGBA")
                bbox = raw_icon.getbbox()
                if bbox:
                    raw_icon = raw_icon.crop(bbox)
                w, h = raw_icon.size
                target_h = 36
                target_w = max(4, int(target_h * (w / h)))
                GLOBAL_ICONS["disease"] = raw_icon.resize((target_w, target_h), Image.LANCZOS)
                logger.info(f"✅ Cached disease icon from {path}")
                break
    except Exception as e:
        logger.error(f"Error caching disease icon: {e}")

    # 4. Water Icon
    try:
        icon_paths = [
            str(Path(__file__).parent / "static" / "water_icon.png"),
        ]
        for path in icon_paths:
            if os.path.exists(path):
                raw_icon = Image.open(path).convert("RGBA")
                bbox = raw_icon.getbbox()
                if bbox:
                    raw_icon = raw_icon.crop(bbox)
                w, h = raw_icon.size
                target_h = 36
                target_w = max(4, int(target_h * (w / h)))
                GLOBAL_ICONS["water"] = raw_icon.resize((target_w, target_h), Image.LANCZOS)
                logger.info(f"✅ Cached water icon from {path}")
                break
    except Exception as e:
        logger.error(f"Error caching water icon: {e}")

    # 5. Cyclone Icon
    try:
        icon_paths = [
            str(Path(__file__).parent / "static" / "cyclone_icon.png"),
            str(Path(__file__).parent / "static" / "storm_icon.png"),
        ]
        for path in icon_paths:
            if os.path.exists(path):
                raw_icon = Image.open(path).convert("RGBA")
                bbox = raw_icon.getbbox()
                if bbox:
                    raw_icon = raw_icon.crop(bbox)
                w, h = raw_icon.size
                target_h = 32
                target_w = max(4, int(target_h * (w / h)))
                GLOBAL_ICONS["cyclone"] = raw_icon.resize((target_w, target_h), Image.LANCZOS)
                logger.info(f"✅ Cached cyclone icon from {path}")
                break
    except Exception as e:
        logger.error(f"Error caching cyclone icon: {e}")

def _precompute_wind_variants():
    """Pre-render wind icon at all (size × angle) combinations used during tile rendering.
    Called once after load_global_assets(); eliminates LANCZOS + BICUBIC per hotspot."""
    global WIND_ICON_VARIANTS
    wind = GLOBAL_ICONS.get("wind")
    if wind is None:
        return
    WIND_ICON_VARIANTS.clear()
    for px_size in range(26, 44, 2):   # 26, 28, 30, ..., 42
        base = wind.resize((px_size, px_size), Image.LANCZOS)
        for slot in range(16):          # 0 → 0°, 1 → 22.5°, ..., 15 → 337.5°
            deg = slot * 22.5
            WIND_ICON_VARIANTS[(px_size, slot)] = base.rotate(
                (360 - deg) % 360, resample=Image.BICUBIC, expand=False
            )
    logger.info(f"✅ Pre-computed {len(WIND_ICON_VARIANTS)} wind icon variants (size×angle).")

def refresh_plot_render_cache():
    """Rebuild the in-memory plot render cache from a single batch DB query.
    Called at startup and after each sync_ai_risk_scores() run.
    Tile rendering reads from this cache instead of hitting the DB per tile."""
    global _plot_render_cache, _global_wind_cache, db_pool
    if db_pool is None:
        return
    conn = db_pool.getconn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                p.id,
                p.user_id,
                ST_AsText(ST_Transform(p.geometry, 4326))       AS geom_wkt,
                ST_AsText(ST_Transform(ST_Centroid(p.geometry), 4326)) AS centroid_wkt,
                ST_XMin(ST_Transform(p.geometry, 4326)),
                ST_YMin(ST_Transform(p.geometry, 4326)),
                ST_XMax(ST_Transform(p.geometry, 4326)),
                ST_YMax(ST_Transform(p.geometry, 4326)),
                pf.ndvi,
                pf.ndmi,
                pf.humidity_pct,
                pf.wind_direction_deg,
                pf.wind_speed_kmh
            FROM plots p
            LEFT JOIN LATERAL (
                SELECT ndvi, ndmi, humidity_pct, wind_direction_deg, wind_speed_kmh
                FROM plot_features
                WHERE plot_id = p.id
                ORDER BY timestamp DESC
                LIMIT 1
            ) pf ON TRUE;
        """)
        rows = cur.fetchall()

        # Also cache the global latest wind for fallback rendering
        cur.execute("""
            SELECT wind_direction_deg, wind_speed_kmh
            FROM plot_features
            WHERE wind_direction_deg IS NOT NULL AND wind_speed_kmh IS NOT NULL
            ORDER BY timestamp DESC LIMIT 1;
        """)
        w_row = cur.fetchone()
        cur.close()

        new_cache: dict[int, dict] = {}
        for row in rows:
            (pid, uid, geom_wkt, centroid_wkt,
             bbox_west, bbox_south, bbox_east, bbox_north,
             ndvi, ndmi, humidity_pct, wind_dir, wind_speed) = row
            new_cache[pid] = {
                "user_id":      uid,
                "geom_wkt":     geom_wkt,
                "centroid_wkt": centroid_wkt,
                "bbox":         (bbox_west, bbox_south, bbox_east, bbox_north),
                "ndvi":         float(ndvi)         if ndvi         is not None else 0.5,
                "ndmi":         float(ndmi)         if ndmi         is not None else 0.5,
                "humidity_pct": float(humidity_pct) if humidity_pct is not None else 50.0,
                "wind_dir":     float(wind_dir)     if wind_dir     is not None else 225.0,
                "wind_speed":   float(wind_speed)   if wind_speed   is not None else 12.0,
            }

        with _plot_render_cache_lock:
            _plot_render_cache.clear()
            _plot_render_cache.update(new_cache)
            if w_row:
                _global_wind_cache["deg"]   = float(w_row[0])
                _global_wind_cache["speed"] = float(w_row[1])

        logger.info("✅ Plot render cache refreshed: {} plots.", len(new_cache))
    except Exception as e:
        logger.error("❌ Failed to refresh plot render cache: {}", e)
    finally:
        db_pool.putconn(conn)


def _plots_in_tile(west: float, south: float, east: float, north: float,
                   user_id_int=None, plot_id_int=None, pad_deg: float = 0.02) -> list[dict]:
    """Return cached plot entries whose bbox intersects the (padded) tile bbox.
    Falls back to an empty list when the cache has not been populated yet."""
    with _plot_render_cache_lock:
        snapshot = dict(_plot_render_cache)
    t_west  = west  - pad_deg
    t_south = south - pad_deg
    t_east  = east  + pad_deg
    t_north = north + pad_deg
    results = []
    for pid, entry in snapshot.items():
        if plot_id_int is not None and pid != plot_id_int:
            continue
        if user_id_int is not None and entry.get("user_id") != user_id_int:
            continue
        bw, bs, be, bn = entry["bbox"]
        if bw <= t_east and be >= t_west and bs <= t_north and bn >= t_south:
            results.append({"id": pid, **entry})
    return results

@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool, EMPTY_TILE_BYTES
    # Startup
    logger.info("🚀 Initializing Database Connection Pool...")
    try:
        db_pool = BlockingThreadedConnectionPool(minconn=5, maxconn=40, dsn=DATABASE_URL)
        logger.info("✅ Database Connection Pool initialized successfully.")
        
        # Ensure spatial + covering indexes exist for high-performance spatial and LATERAL JOIN queries
        conn = db_pool.getconn()
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
            db_pool.putconn(conn)
    except Exception as e:
        logger.error(f"❌ Failed to initialize database: {e}")
        raise e

    # Pre-encode and cache transparent empty tile globally
    try:
        img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        EMPTY_TILE_BYTES = buf.getvalue()
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
    indices_dir = Path(__file__).parent.parent / "data" / "processed" / "indices" / "latest"
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
    
    if db_pool:
        logger.info("🛑 Closing Database Connection Pool...")
        db_pool.closeall()
        logger.info("✅ Database Connection Pool closed.")

app = FastAPI(
    title="Satellite Team API",
    description="API สำหรับส่งมอบ Geo-features ให้กับ AI Team และ App Team",
    version="1.0.0",
    lifespan=lifespan
)

# Enable GZip compression to shrink JSON payloads by up to 90%, speeding up network transfer
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Heaven's Eye BE Integration routers
app.include_router(auth_router, prefix="/api/v1/auth", tags=["Authentication"])
app.include_router(plots_router, prefix="/api/v1/plots", tags=["Plot Management"])
app.include_router(notifications_router, prefix="/api/v1/notifications", tags=["Notifications"])

@app.get("/api/v1/health", tags=["Health"])
def health_check():
    return {"status": "ok", "message": "Heaven Eye Backend is running on FastAPI"}

# Mount static uploads directory for plot photographs
import os
from fastapi.staticfiles import StaticFiles
os.makedirs(Path(__file__).parent / "static" / "uploads", exist_ok=True)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


# ==========================================
# Helper Utilities
# ==========================================
def parse_plot_id(plot_id_str: str) -> int:
    """แปลง plot_id เช่น 'PLT-001' หรือ '1' ให้เป็น integer ID"""
    if isinstance(plot_id_str, int):
        return plot_id_str
    digits = "".join(filter(str.isdigit, plot_id_str))
    if digits:
        return int(digits)
    # ถ้าไม่มีตัวเลข ให้ลองแปลงโดยตรง หรือยกเว้น error
    try:
        return int(plot_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid plot_id format: {plot_id_str}. Must contain digits.")

def format_plot_id(plot_id_int: int) -> str:
    """แปลง integer ID กลับเป็น string เช่น 'PLT-001'"""
    return f"PLT-{plot_id_int:03d}"


def safe_float(value: Any, default: float = 0.0) -> float:
    """แปลงค่าเป็น float แบบปลอดภัย"""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    """แปลงค่าเป็น int แบบปลอดภัย"""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_confidence(value: Any) -> Literal["high", "medium", "low"]:
    """Normalize confidence values from DB/source systems to contract values."""
    raw_value = str(value).strip().lower() if value is not None else ""
    mapping = {
        "high": "high",
        "h": "high",
        "medium": "medium",
        "m": "medium",
        "nominal": "medium",
        "n": "medium",
        "low": "low",
        "l": "low",
    }
    return mapping.get(raw_value, "low")


# Ordered column list for SELECT from plot_features.
# !! Keep row indices in sync with build_plot_feature_response() below !!
PLOT_FEATURE_SELECT_COLUMNS = """
    plot_id,            -- row[0]
    timestamp,          -- row[1]
    data_freshness_days,-- row[2]
    cloud_cover_pct,    -- row[3]
    confidence,         -- row[4]
    ndvi,               -- row[5]
    ndmi,               -- row[6]
    nbr,                -- row[7]
    rain_7d_mm,         -- row[8]
    humidity_pct,       -- row[9]
    wind_speed_kmh,     -- row[10]
    hotspot_count_24h,  -- row[11]
    hotspot_count_7d,   -- row[12]
    nearest_hotspot_km, -- row[13]
    spi_30d             -- row[14]
"""

SUPPORTED_HISTORY_INDICES = {"ndvi", "ndmi", "nbr"}

# ==========================================
# Schemas (Pydantic Models)
# ==========================================
class IndicesFeature(BaseModel):
    ndvi: float
    ndmi: float
    nbr: float

class WeatherFeature(BaseModel):
    rain_7d_mm: float
    humidity_pct: float
    wind_speed_kmh: float

class FireFeature(BaseModel):
    hotspot_count_24h: int
    hotspot_count_7d: int
    nearest_hotspot_km: float

class SPIFeature(BaseModel):
    spi_30d: float


ConfidenceLevel = Literal["high", "medium", "low"]


class PlotFeatureResponse(BaseModel):
    plot_id: str
    timestamp: datetime
    data_freshness_days: int
    cloud_cover_pct: float
    confidence: ConfidenceLevel
    indices: IndicesFeature
    weather: WeatherFeature
    fire: FireFeature
    spi: SPIFeature


class PlotHistoryResponse(BaseModel):
    plot_id: str
    series: List[Dict[str, Any]]


class PlotCreateResponse(BaseModel):
    status: Literal["success"]
    message: str
    plot_id: str

class PlotCreateRequest(BaseModel):
    plot_id: str
    user_id: str
    geometry: Dict[str, Any]  # GeoJSON Geometry object
    crop_type: str
    province: str

# Models for Hotspots GeoJSON
class HotspotGeometry(BaseModel):
    type: str = "Point"
    coordinates: List[float]  # [lon, lat]

class HotspotProperties(BaseModel):
    brightness: float
    confidence: str
    acq_time: datetime
    satellite: str

class HotspotFeature(BaseModel):
    type: str = "Feature"
    geometry: HotspotGeometry
    properties: HotspotProperties

class HotspotCollection(BaseModel):
    type: str = "FeatureCollection"
    features: List[HotspotFeature]


def build_plot_feature_response(row: Sequence[Any]) -> PlotFeatureResponse:
    """Map a plot_features row into the nested API contract."""
    return PlotFeatureResponse(
        plot_id=format_plot_id(safe_int(row[0])),
        timestamp=row[1],
        data_freshness_days=safe_int(row[2]),
        cloud_cover_pct=safe_float(row[3]),
        confidence=normalize_confidence(row[4]),
        indices=IndicesFeature(
            ndvi=safe_float(row[5]),
            ndmi=safe_float(row[6]),
            nbr=safe_float(row[7]),
        ),
        weather=WeatherFeature(
            rain_7d_mm=safe_float(row[8]),
            humidity_pct=safe_float(row[9]),
            wind_speed_kmh=safe_float(row[10]),
        ),
        fire=FireFeature(
            hotspot_count_24h=safe_int(row[11]),
            hotspot_count_7d=safe_int(row[12]),
            nearest_hotspot_km=safe_float(row[13], default=999.0),
        ),
        spi=SPIFeature(
            spi_30d=safe_float(row[14]),
        ),
    )

# ==========================================
# Database Connection Dependency
# ==========================================
def get_db():
    global db_pool
    if db_pool is None:
        conn = psycopg2.connect(DATABASE_URL)
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        conn = db_pool.getconn()
        try:
            yield conn
        except Exception:
            # Roll back any aborted transaction so the connection is clean
            # before it goes back into the pool
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            db_pool.putconn(conn)

# ==========================================
# Routes
# ==========================================

@app.get("/")
def read_root():
    return {"message": "Welcome to Satellite Team API", "status": "running"}

@app.get("/api/v1/features", response_model=List[PlotFeatureResponse])
def get_all_latest_features(conn=Depends(get_db)):
    """ดึง feature ล่าสุดของทุกแปลงพร้อมกัน สำหรับส่งให้ AI Team (Batch Processing)"""
    cur = conn.cursor()
    # DISTINCT ON (plot_id) เพื่อดึง record ล่าสุดของแต่ละแปลง
    query = f"""
        SELECT DISTINCT ON (plot_id)
               {PLOT_FEATURE_SELECT_COLUMNS}
        FROM plot_features
        ORDER BY plot_id, timestamp DESC;
    """
    cur.execute(query)
    rows = cur.fetchall()
    cur.close()

    return [build_plot_feature_response(row) for row in rows]

@app.get("/api/v1/features/{plot_id}", response_model=PlotFeatureResponse)
def get_latest_feature(plot_id: str, response: Response, conn=Depends(get_db)):
    """ดึง feature ล่าสุดของแปลงเกษตรสำหรับส่งให้ AI Team"""
    plot_id_int = parse_plot_id(plot_id)
    cur = conn.cursor()
    query = f"""
        SELECT {PLOT_FEATURE_SELECT_COLUMNS}
        FROM plot_features
        WHERE plot_id = %s
        ORDER BY timestamp DESC
        LIMIT 1;
    """
    cur.execute(query, (plot_id_int,))
    r = cur.fetchone()
    cur.close()

    if not r:
        raise HTTPException(status_code=404, detail="PLOT_NOT_FOUND or NO_DATA_AVAILABLE")

    # Features update only during daily ingestion; 5-min fresh cache, 1-hour stale window.
    response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=3600"
    return build_plot_feature_response(r)

@app.get("/api/v1/features/{plot_id}/history", response_model=PlotHistoryResponse)
def get_feature_history(
    plot_id: str,
    start_date: datetime = Query(..., description="ISO date start boundary"),
    end_date: datetime = Query(..., description="ISO date end boundary"),
    indices: Optional[str] = Query(default=None, description="comma-separated list of indices"),
    conn=Depends(get_db)
):
    """ดึง time series ของ feature สำหรับ trend analysis"""
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="end_date must be greater than or equal to start_date")

    selected_indices = ["ndvi", "ndmi", "nbr"]
    if indices:
        selected_indices = [item.strip().lower() for item in indices.split(",") if item.strip()]
        invalid_indices = sorted(set(selected_indices) - SUPPORTED_HISTORY_INDICES)
        if invalid_indices:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported indices requested: {', '.join(invalid_indices)}"
            )

    plot_id_int = parse_plot_id(plot_id)
    cur = conn.cursor()
    query = """
        SELECT timestamp, ndvi, ndmi, nbr
        FROM plot_features
        WHERE plot_id = %s
          AND timestamp >= %s
          AND timestamp <= %s
        ORDER BY timestamp ASC;
    """
    cur.execute(query, (plot_id_int, start_date, end_date))
    rows = cur.fetchall()
    cur.close()

    if not rows:
        raise HTTPException(status_code=404, detail="NO_DATA_AVAILABLE")

    series = []
    for r in rows:
        point = {"timestamp": r[0]}
        if "ndvi" in selected_indices:
            point["ndvi"] = safe_float(r[1])
        if "ndmi" in selected_indices:
            point["ndmi"] = safe_float(r[2])
        if "nbr" in selected_indices:
            point["nbr"] = safe_float(r[3])
        series.append(point)

    return PlotHistoryResponse(plot_id=format_plot_id(plot_id_int), series=series)

@app.post("/api/v1/plots", status_code=201, response_model=PlotCreateResponse)
def create_plot(request: PlotCreateRequest, conn=Depends(get_db)):
    """App Team ส่ง polygon ของแปลงมาให้ Satellite Team เก็บ"""
    plot_id_int = parse_plot_id(request.plot_id)
    user_id_int = parse_plot_id(request.user_id)
    geom_geojson = json.dumps(request.geometry)
    
    cur = conn.cursor()
    try:
        # ใช้ ST_SetSRID ก่อน transform เพื่อให้ geometry จาก GeoJSON เป็น WGS84 อย่างถูกต้อง
        query = """
            WITH input_geom AS (
                SELECT ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), 32647) AS geometry
            )
            INSERT INTO plots (id, user_id, plot_name, area_size, geometry)
            SELECT
                %s,
                %s,
                %s,
                ST_Area(geometry) / 10000.0,
                geometry
            FROM input_geom
            ON CONFLICT (id) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                plot_name = EXCLUDED.plot_name,
                area_size = EXCLUDED.area_size,
                geometry = EXCLUDED.geometry
            RETURNING id;
        """
        cur.execute(query, (geom_geojson, plot_id_int, user_id_int, request.plot_id))
        inserted_id = cur.fetchone()[0]
        conn.commit()
        return PlotCreateResponse(
            status="success",
            message=f"Plot {request.plot_id} successfully created/updated.",
            plot_id=format_plot_id(inserted_id)
        )
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    finally:
        cur.close()

@app.get("/api/v1/hotspots", response_model=HotspotCollection)
def get_hotspots(
    bbox: str = Query(..., description="min_lon,min_lat,max_lon,max_lat"),
    hours: int = Query(default=24, ge=1, le=168),
    response: Response = None,
    conn=Depends(get_db)
):
    """ดึงจุดความร้อนในพื้นที่ที่กำหนด"""
    try:
        coords = [float(c) for c in bbox.split(",")]
        if len(coords) != 4:
            raise ValueError()
    except ValueError:
        raise HTTPException(status_code=400, detail="Bbox must be in format min_lon,min_lat,max_lon,max_lat")
        
    min_lon, min_lat, max_lon, max_lat = coords
    cutoff_time = datetime.now() - timedelta(hours=hours)
    
    cur = conn.cursor()
    # ดึงเฉพาะจุดที่อยู่ในขอบเขตพิกัด
    query = """
        SELECT latitude, longitude, brightness, confidence, acq_time, satellite
        FROM hotspots
        WHERE acq_time >= %s
          AND longitude >= %s AND latitude >= %s
          AND longitude <= %s AND latitude <= %s
        ORDER BY acq_time DESC;
    """
    cur.execute(query, (cutoff_time, min_lon, min_lat, max_lon, max_lat))
    rows = cur.fetchall()
    cur.close()
    
    features = []
    for r in rows:
        lat, lon, brightness, confidence, acq_time, satellite = r
        features.append(HotspotFeature(
            geometry=HotspotGeometry(coordinates=[float(lon), float(lat)]),
            properties=HotspotProperties(
                brightness=safe_float(brightness),
                confidence=normalize_confidence(confidence),
                acq_time=acq_time,
                satellite=str(satellite) if satellite else "VIIRS_SNPP"
            )
        ))

    # Hotspots update hourly from VIIRS; 5-min fresh cache, 10-min stale window.
    if response is not None:
        response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=600"
    return HotspotCollection(features=features)

@functools.lru_cache(maxsize=1024)
def _render_index_tile_cached(layer: str, layer_upper: str, z: int, x: int, y: int) -> bytes:
    indices_dir = Path(__file__).parent.parent / "data" / "processed" / "indices"
    latest_file = indices_dir / "latest" / f"latest_{layer_upper}.tif"
    
    if latest_file.exists():
        tif_file = latest_file
    else:
        if not indices_dir.exists():
            return b""
        scene_dirs = [d for d in indices_dir.iterdir() if d.is_dir() and d.name != "latest"]
        if not scene_dirs:
            return b""
        target_scene = max(scene_dirs, key=lambda d: d.stat().st_mtime)
        tif_file = target_scene / f"{target_scene.name}_{layer_upper}.tif"
        if not tif_file.exists():
            return b""
            
    try:
        with rasterio.open(tif_file) as src:
            bbox = mercantile.xy_bounds(x, y, z)
            
            dst_data = np.zeros((256, 256), dtype=np.float32)
            dst_transform = rasterio.transform.from_bounds(
                bbox.left, bbox.bottom, bbox.right, bbox.top, 256, 256
            )
            
            reproject(
                source=rasterio.band(src, 1),
                destination=dst_data,
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=dst_transform,
                dst_crs='EPSG:3857',
                resampling=Resampling.bilinear,
                init_dest_nodata=True
            )
            
            nodata_val = src.nodata if src.nodata is not None else -9999.0
            
            if layer == "ndvi":
                xp = [-1.0, -0.1, 0.2, 0.4, 0.8, 1.0]
                fp_r = [180, 215, 245, 190,  34,   0]
                fp_g = [ 30, 100, 220, 230, 139,  68]
                fp_b = [ 30,  40, 110,  90,  34,  27]
            elif layer == "ndmi":
                xp = [-1.0, -0.8, -0.2, 0.2, 0.8, 1.0]
                fp_r = [150, 210, 245, 150,  30,   0]
                fp_g = [ 75, 180, 235, 200, 100,  10]
                fp_b = [ 30, 140, 180, 250, 250, 150]
            else: # nbr
                xp = [-1.0, -0.8, -0.2, 0.1, 0.8, 1.0]
                fp_r = [100, 220, 245, 215,  34,   0]
                fp_g = [  0,  20, 150, 220, 139,  68]
                fp_b = [ 80,  20,  50, 160,  34,  27]
            
            red = np.interp(dst_data, xp, fp_r).astype(np.uint8)
            green = np.interp(dst_data, xp, fp_g).astype(np.uint8)
            blue = np.interp(dst_data, xp, fp_b).astype(np.uint8)
            
            is_nodata = np.isnan(dst_data) | (dst_data == nodata_val) | (dst_data < -1.0) | (dst_data > 1.0)
            
            if np.all(is_nodata | (dst_data == 0.0)):
                return b""
                
            alpha = np.where(is_nodata, 0, 255).astype(np.uint8)
            
            rgba = np.stack([red, green, blue, alpha], axis=-1)
            
            img = Image.fromarray(rgba, "RGBA")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
    except Exception as e:
        print(f"Error rendering raster tile: {e}")
        return b""

@app.get("/api/v1/tiles/{layer}/{z}/{x}/{y}.png")
def get_tile(
    layer: str,
    z: int,
    x: int,
    y: int,
    request: Request = None,
    user_id: Optional[str] = Query(None),
    plot_id: Optional[str] = Query(None),
):
    layer = layer.lower()
    if layer not in {"ndvi", "ndmi", "nbr", "hotspot", "burn_scar", "disease", "drought", "flood"}:
        raise HTTPException(status_code=400, detail=f"Unsupported layer: {layer}")

    # Secure user_id/plot_id filtering (Data Leak Prevention)
    if layer in {"disease", "drought"}:
        if not user_id and not plot_id:
            raise HTTPException(
                status_code=401,
                detail=f"Authentication required: user_id or plot_id query parameter is required for the '{layer}' layer."
            )
        try:
            if plot_id:
                parse_plot_id(plot_id)
            elif user_id:
                parse_plot_id(user_id)
        except Exception:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid identifier format provided for the '{layer}' layer."
            )

    # 1. Compute and validate ETag before rendering
    import hashlib
    etag_raw = f"{layer}_{z}_{x}_{y}_{user_id or ''}_{plot_id or ''}"
    # Expire etag every hour so fresh ingestion matches hourly updates
    current_hour = datetime.utcnow().strftime("%Y-%m-%d-%H")
    etag_raw += f"_{current_hour}"
    etag_val = hashlib.md5(etag_raw.encode()).hexdigest()
    etag = f'"{etag_val}"'

    if request:
        if_none_match = request.headers.get("if-none-match")
        if if_none_match == etag:
            return Response(
                status_code=304,
                headers={
                    "Cache-Control": "public, max-age=300, stale-while-revalidate=600",
                    "ETag": etag
                }
            )

    def empty_tile():
        global EMPTY_TILE_BYTES
        if EMPTY_TILE_BYTES is None:
            img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            EMPTY_TILE_BYTES = buf.getvalue()
        return Response(
            content=EMPTY_TILE_BYTES,
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=86400, stale-while-revalidate=172800",
                "ETag": etag
            }
        )

    if layer in {"ndvi", "ndmi", "nbr"}:
        layer_upper = layer.upper()
        
        # Note: synchronous download_from_s3 removed to prevent blocking API threads.
        # lifespan pre-fetches it. If it's missing, we return empty tile safely.
        
        png_bytes = _render_index_tile_cached(layer, layer_upper, z, x, y)
        if not png_bytes:
            return empty_tile()
        return Response(content=png_bytes, media_type="image/png")

    elif layer in {"hotspot", "burn_scar", "disease", "drought", "flood"}:
        # In-process cache check — clears itself every hour when the hour key changes
        global _vtile_cache_hour
        with _vtile_lock:
            if _vtile_cache_hour != current_hour:
                _vector_tile_cache.clear()
                _vtile_cache_hour = current_hour
            cached_png = _vector_tile_cache.get(etag_val)
        if cached_png is not None:
            return Response(
                content=cached_png,
                media_type="image/png",
                headers={
                    "Cache-Control": "public, max-age=300, stale-while-revalidate=600",
                    "ETag": etag,
                }
            )

        global db_pool
        # disease/drought serve entirely from in-memory cache; skip pool acquisition.
        _needs_db = layer not in {"disease", "drought"}
        conn = db_pool.getconn() if _needs_db else None
        cur = None
        try:
            bounds = mercantile.bounds(x, y, z)
            west, south, east, north = bounds.west, bounds.south, bounds.east, bounds.north

            img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            
            xy_bounds = mercantile.xy_bounds(x, y, z)
            min_x, min_y, max_x, max_y = xy_bounds.left, xy_bounds.bottom, xy_bounds.right, xy_bounds.top
            
            # Buffer distance in Web Mercator meters equivalent to 64 pixels (prevent icon clipping at tile boundaries)
            tile_w = max_x - min_x
            buffer_meters = tile_w * (64.0 / 256.0)
            
            def to_pixels(lng, lat):
                mx = lng * 20037508.34 / 180.0
                my = math.log(math.tan((90.0 + lat) * math.pi / 360.0)) / (math.pi / 180.0)
                my = my * 20037508.34 / 180.0
                
                px = 256.0 * (mx - min_x) / (max_x - min_x)
                py = 256.0 * (max_y - my) / (max_y - min_y)
                return px, py
                
            cur = conn.cursor() if conn is not None else None  # Closed in finally block below
            
            if layer == "hotspot":
                # 1. Draw plots colored by fire_risk_score — from in-memory cache (no DB query)
                plot_id_int = None
                if plot_id:
                    try:
                        plot_id_int = parse_plot_id(plot_id)
                    except Exception:
                        pass

                user_id_int = None
                if user_id and plot_id_int is None:
                    try:
                        user_id_int = parse_plot_id(user_id)
                    except Exception:
                        pass

                cached_plots = _plots_in_tile(west, south, east, north,
                                              user_id_int=user_id_int, plot_id_int=plot_id_int)

                for entry in cached_plots:
                    pid = entry["id"]
                    fire_risk = entry["fire_risk"]
                    plot_geom_wkt = entry["geom_wkt"]
                    active_risk = fire_risk
                    
                    if plot_geom_wkt:
                        try:
                            geom = wkt_loads(plot_geom_wkt)
                            
                            # Color-code based on fire risk score — mirrors riskState.ts thresholds
                            # and alert_engine.py so map colours = notification severity:
                            # >= 0.80: อันตราย / Danger (Red)    rgb(220, 38, 38)  --tas-danger
                            # >= 0.60: เฝ้าระวัง / Warn  (Orange)  rgb(255, 141, 40) --tas-warn
                            #  < 0.60: ปกติดี / OK      (Green)   rgb(64, 171, 104) --tas-ok
                            if active_risk >= 0.80:
                                fill_color = (220, 38, 38, 205)        # --tas-danger (อันตราย)
                                outline_color = (185, 28, 28, 220)
                            elif active_risk >= 0.60:
                                fill_color = (255, 141, 40, 205)       # --tas-warn (เฝ้าระวัง)
                                outline_color = (220, 100, 10, 220)
                            else:
                                fill_color = (64, 171, 104, 205)       # --tas-ok (ปกติดี)
                                outline_color = (45, 135, 78, 220)

                            def draw_plot_poly(g):
                                if g.geom_type == 'Polygon':
                                    ext_coords = [to_pixels(lon, lat) for lon, lat in g.exterior.coords]
                                    if len(ext_coords) >= 3:
                                        draw.polygon(ext_coords, fill=fill_color, outline=outline_color, width=2)
                                    for interior in g.interiors:
                                        int_coords = [to_pixels(lon, lat) for lon, lat in interior.coords]
                                        if len(int_coords) >= 3:
                                            draw.polygon(int_coords, fill=(0, 0, 0, 0), outline=outline_color, width=2)
                                elif g.geom_type == 'MultiPolygon':
                                    for poly in g.geoms:
                                        draw_plot_poly(poly)
                                        
                            draw_plot_poly(geom)
                        except Exception as e:
                            print(f"Error drawing fire colored plot boundary: {e}")

                # 2. Draw hotspots and wind vectors
                # Spatial lateral join to retrieve wind speed and direction from the nearest plot_features record for each hotspot,
                # with highly dynamic, locally accurate meteorological rendering.
                query = """
                    SELECT 
                        h.longitude, 
                        h.latitude, 
                        h.brightness, 
                        h.confidence,
                        w.wind_direction_deg,
                        w.wind_speed_kmh
                    FROM hotspots h
                    LEFT JOIN LATERAL (
                        SELECT pf.wind_direction_deg, pf.wind_speed_kmh
                        FROM plots p
                        JOIN plot_features pf ON p.id = pf.plot_id
                        WHERE pf.wind_direction_deg IS NOT NULL AND pf.wind_speed_kmh IS NOT NULL
                        ORDER BY p.geometry <-> h.geometry, pf.timestamp DESC
                        LIMIT 1
                    ) w ON TRUE
                    WHERE ST_Intersects(
                        h.geometry,
                        ST_Buffer(
                            ST_Transform(
                                ST_MakeEnvelope(%s, %s, %s, %s, 4326),
                                32647
                            ),
                            %s
                        )
                    );
                """
                cur.execute(query, (west, south, east, north, buffer_meters))
                rows = cur.fetchall()
                
                # Use globally preloaded fire icon and wind icon
                fire_icon = GLOBAL_ICONS.get("fire")
                wind_icon = GLOBAL_ICONS.get("wind")

                # Use cached global wind as fallback (refreshed hourly after sync)
                global_wind_deg   = _global_wind_cache["deg"]
                global_wind_speed = _global_wind_cache["speed"]

                for row in rows:
                    lon, lat, brightness, confidence, row_wind_deg, row_wind_speed = row
                    px, py = to_pixels(float(lon), float(lat))
                    
                    # Resolve spatial wind direction (fallback to global latest if NULL)
                    wind_deg = float(row_wind_deg) if row_wind_deg is not None else global_wind_deg
                    wind_speed = float(row_wind_speed) if row_wind_speed is not None else global_wind_speed
                    
                    # 1. Draw a highly visible wind direction vector arrow (meteorological wind blows FROM, arrow points TO)
                    arrow_dir = (wind_deg - 180) % 360
                    rad = math.radians(arrow_dir)
                    
                    # Center of wind arrow is offset bottom-right (wx = px + 22, wy = py + 14) so it aligns perfectly with the larger flame
                    wx = px + 22
                    wy = py + 14
                    
                    # Larger high-contrast base anchor dot
                    draw.ellipse([wx-3, wy-3, wx+3, wy+3], fill=(255, 255, 255, 255))
                    
                    if wind_icon is not None:
                        # Dynamic wind icon size based on wind speed (26–42px), rounded to nearest
                        # even step so it hits the pre-computed variant table (no per-hotspot resize).
                        arr_size_raw = max(26, min(42, int(18 + wind_speed * 1.0)))
                        arr_size = round(arr_size_raw / 2) * 2   # snap to 26, 28, ..., 42
                        angle_slot = round(arrow_dir / 22.5) % 16

                        rotated_w = WIND_ICON_VARIANTS.get((arr_size, angle_slot))
                        if rotated_w is None:
                            # Fallback for edge cases (variants not yet built)
                            resized_w = wind_icon.resize((arr_size, arr_size), Image.LANCZOS)
                            rotated_w = resized_w.rotate((360 - arrow_dir) % 360, resample=Image.BICUBIC, expand=False)

                        # Paste the rotated wind icon centered at (wx, wy)
                        img.paste(rotated_w, (int(wx - arr_size / 2), int(wy - arr_size / 2)), mask=rotated_w)
                    else:
                        # Fallback: Draw wind line with a strong dark drop shadow for maximum 3D popup and high-contrast map visibility
                        arr_len = max(26, min(44, 20 + wind_speed * 1.2))
                        ex = wx + arr_len * math.sin(rad)
                        ey = wy - arr_len * math.cos(rad)
                        
                        draw.line([(wx+0.8, wy+0.8), (ex+0.8, ey+0.8)], fill=(0, 0, 0, 180), width=5)
                        draw.line([(wx, wy), (ex, ey)], fill=(0, 225, 255, 255), width=3)
                        
                        # Massive sharp arrowhead pointing in the direction of the wind
                        hs = 8
                        hlx = ex - hs * math.sin(rad + math.radians(35))
                        hly = ey + hs * math.cos(rad + math.radians(35))
                        hrx = ex - hs * math.sin(rad - math.radians(35))
                        hry = ey + hs * math.cos(rad - math.radians(35))
                        
                        # Draw thick arrowhead drop shadow & arrow body
                        draw.polygon([(ex+0.8, ey+0.8), (hlx+0.8, hly+0.8), (hrx+0.8, hry+0.8)], fill=(0, 0, 0, 180))
                        draw.polygon([(ex, ey), (hlx, hly), (hrx, hry)], fill=(0, 225, 255, 255))
                    
                    # 2. Render the custom PNG fire icon
                    if fire_icon is not None:
                        # Paste the actual custom fire icon image centered at the coordinates (no extra circular halos/css icons)
                        iw, ih = fire_icon.size
                        img.paste(fire_icon, (int(px - iw / 2), int(py - ih / 2)), mask=fire_icon)
                    else:
                        # Fallback: Outer flame body (Precise asymmetrical double-peak shape matching the 3rd image)
                        outer_coords = [
                            (px - 1, py - 14),   # Main left peak tip
                            (px - 5, py - 9),    # Left upper shoulder
                            (px - 9, py - 3),    # Left waist curve
                            (px - 11, py + 3),   # Left hip swell
                            (px - 10, py + 9),   # Left bottom base
                            (px - 5, py + 13),   # Bottom left curve
                            (px, py + 14),       # Bottom center
                            (px + 5, py + 13),   # Bottom right curve
                            (px + 10, py + 9),   # Right bottom base
                            (px + 11, py + 4),   # Right hip swell
                            (px + 9, py - 1),    # Right waist curve
                            (px + 7, py - 4),    # Dip bottom right
                            (px + 8, py - 7),    # Secondary right peak tip
                            (px + 5, py - 3),    # Dip center between peaks
                            (px + 3, py - 8)     # Slope leading back to main tip
                        ]
                        draw.polygon(outer_coords, fill=(255, 107, 24, 255), outline=None)
            elif layer == "disease":
                # Parse plot_id or user_id to integer if provided
                plot_id_int = None
                if plot_id:
                    try:
                        plot_id_int = parse_plot_id(plot_id)
                    except Exception:
                        pass

                user_id_int = None
                if user_id and plot_id_int is None:
                    try:
                        user_id_int = parse_plot_id(user_id)
                    except Exception:
                        pass

                # Use in-memory plot render cache — no DB query for plot/risk/feature data
                disease_icon = GLOBAL_ICONS.get("disease")
                wind_icon = GLOBAL_ICONS.get("wind")
                global_wind_deg   = _global_wind_cache["deg"]
                global_wind_speed = _global_wind_cache["speed"]

                for entry in _plots_in_tile(west, south, east, north,
                                            user_id_int=user_id_int, plot_id_int=plot_id_int):
                    plot_geom_wkt = entry["geom_wkt"]
                    centroid_wkt  = entry["centroid_wkt"]
                    risk_score    = entry["disease_risk"]
                    row_ndvi      = entry["ndvi"]
                    row_wind_deg  = entry["wind_dir"]
                    row_wind_speed = entry["wind_speed"]

                    # 1. First draw the colored agricultural plot boundary polygon if it exists
                    if plot_geom_wkt:
                        try:
                            geom = wkt_loads(plot_geom_wkt)

                            # Determine NDVI 4-level color based on the user's uploaded spec (non-green tones):
                            # -1.0 to 0.0: Dead plant or object (Gray)
                            # 0.0 to 0.33: Unhealthy plant (Crimson Red)
                            # 0.33 to 0.66: Moderately healthy plant (Pumpkin Orange)
                            # 0.66 to 1.0: Very healthy plant (Golden Amber/Yellow)
                            plot_ndvi = row_ndvi  # already a float from cache
                            
                            if plot_ndvi <= 0.0:
                                fill_color = (158, 158, 158, 205)      # Gray (Dead plant / worst level)
                                outline_color = (117, 117, 117, 220)
                            elif plot_ndvi <= 0.33:
                                fill_color = (211, 47, 47, 205)        # Crimson Red (Unhealthy)
                                outline_color = (183, 28, 28, 220)
                            elif plot_ndvi <= 0.66:
                                fill_color = (245, 124, 0, 205)        # Pumpkin Orange (Moderately healthy)
                                outline_color = (230, 81, 0, 220)
                            else:
                                fill_color = (74, 138, 42, 205)        # Green (Very healthy)
                                outline_color = (56, 105, 32, 220)

                            def draw_plot_poly(g):
                                if g.geom_type == 'Polygon':
                                    ext_coords = [to_pixels(lon, lat) for lon, lat in g.exterior.coords]
                                    if len(ext_coords) >= 3:
                                        draw.polygon(ext_coords, fill=fill_color, outline=outline_color, width=2)
                                    for interior in g.interiors:
                                        int_coords = [to_pixels(lon, lat) for lon, lat in interior.coords]
                                        if len(int_coords) >= 3:
                                            draw.polygon(int_coords, fill=(0, 0, 0, 0), outline=outline_color, width=2)
                                elif g.geom_type == 'MultiPolygon':
                                    for poly in g.geoms:
                                        draw_plot_poly(poly)
                                        
                            draw_plot_poly(geom)
                        except Exception as e:
                            print(f"Error drawing colored plot boundary: {e}")

                    # 2. Render crop disease icon if the plot is in medium or worst level (NDVI <= 0.66)
                    plot_ndvi = row_ndvi  # already a float from cache
                    
                    if plot_ndvi <= 0.66:
                        if not centroid_wkt:
                            continue
                        
                        try:
                            pt = wkt_loads(centroid_wkt)
                            lon, lat = pt.x, pt.y
                            px, py = to_pixels(lon, lat)
                            
                            # Draw custom disease PNG icon, or a beautiful non-green virus fallback polygon
                            if disease_icon is not None:
                                iw, ih = disease_icon.size
                                img.paste(disease_icon, (int(px - iw / 2), int(py - ih / 2)), mask=disease_icon)
                            else:
                                # Premium non-green fallback
                                draw.ellipse([px-12, py-12, px+12, py+12], fill=(211, 47, 47, 240), outline=(255, 193, 7, 255), width=2)
                                for ang in range(0, 360, 45):
                                    r_ang = math.radians(ang)
                                    sx = px + 15 * math.sin(r_ang)
                                    sy = py - 15 * math.cos(r_ang)
                                    draw.ellipse([sx-3, sy-3, sx+3, sy+3], fill=(255, 193, 7, 255))
                        except Exception as e:
                            print(f"Error rendering individual disease plot marker: {e}")

            elif layer == "drought":
                # Parse plot_id or user_id to integer if provided
                plot_id_int = None
                if plot_id:
                    try:
                        plot_id_int = parse_plot_id(plot_id)
                    except Exception:
                        pass

                user_id_int = None
                if user_id and plot_id_int is None:
                    try:
                        user_id_int = parse_plot_id(user_id)
                    except Exception:
                        pass

                # Use in-memory plot render cache — no DB query for plot/risk/feature data
                water_icon = GLOBAL_ICONS.get("water")

                for entry in _plots_in_tile(west, south, east, north,
                                            user_id_int=user_id_int, plot_id_int=plot_id_int):
                    plot_geom_wkt = entry["geom_wkt"]
                    centroid_wkt  = entry["centroid_wkt"]
                    row_ndmi      = entry["ndmi"]
                    row_humidity  = entry["humidity_pct"]

                    # 1. Draw the colored agricultural plot boundary polygon if it exists
                    if plot_geom_wkt:
                        try:
                            geom = wkt_loads(plot_geom_wkt)

                            # Determine NDMI 3-level color based on the user's specs:
                            # 0.4 to 1.0: Healthy, lush vegetation or dense forest with no water stress (Green)
                            # 0.2 to 0.4: Sparse or stressed crops; moderate canopy cover with mild water stress (Orange)
                            # < 0.2: Bare soil, urban structures, or sparse canopy with high water stress (Red)
                            ndmi_val = row_ndmi  # already a float from cache
                            
                            if ndmi_val >= 0.4:
                                fill_color = (74, 138, 42, 205)        # Green (Optimal / Healthy)
                                outline_color = (56, 105, 32, 220)
                            elif ndmi_val >= 0.2:
                                fill_color = (245, 124, 0, 205)        # Orange (Mild Water Demand)
                                outline_color = (230, 81, 0, 220)
                            else:
                                fill_color = (211, 47, 47, 205)        # Crimson Red (High Water Demand)
                                outline_color = (183, 28, 28, 220)

                            def draw_plot_poly(g):
                                if g.geom_type == 'Polygon':
                                    ext_coords = [to_pixels(lon, lat) for lon, lat in g.exterior.coords]
                                    if len(ext_coords) >= 3:
                                        draw.polygon(ext_coords, fill=fill_color, outline=outline_color, width=2)
                                    for interior in g.interiors:
                                        int_coords = [to_pixels(lon, lat) for lon, lat in interior.coords]
                                        if len(int_coords) >= 3:
                                            draw.polygon(int_coords, fill=(0, 0, 0, 0), outline=outline_color, width=2)
                                elif g.geom_type == 'MultiPolygon':
                                    for poly in g.geoms:
                                        draw_plot_poly(poly)
                                        
                            draw_plot_poly(geom)
                        except Exception as e:
                            print(f"Error drawing drought colored plot boundary: {e}")

                    # 2. Render warning indicator if the plot is in medium or worst level (NDMI < 0.4)
                    ndmi_val = row_ndmi       # already a float from cache
                    humidity_val = row_humidity

                    if ndmi_val < 0.4:
                        if not centroid_wkt:
                            continue
                        
                        try:
                            pt = wkt_loads(centroid_wkt)
                            lon, lat = pt.x, pt.y
                            px, py = to_pixels(lon, lat)
                            
                            if water_icon is not None:
                                iw, ih = water_icon.size
                                img.paste(water_icon, (int(px - iw / 2), int(py - ih / 2)), mask=water_icon)
                            else:
                                # Fallback circle with exclamation / droplet
                                if humidity_val > 60.0:
                                    draw.ellipse([px-10, py-10, px+10, py+10], fill=(33, 150, 243, 240), outline=(255, 255, 255, 255), width=2)
                                    draw.polygon([(px, py-14), (px-5, py-5), (px+5, py-5)], fill=(33, 150, 243, 240))
                                else:
                                    draw.ellipse([px-10, py-10, px+10, py+10], fill=(211, 47, 47, 240), outline=(255, 193, 7, 255), width=2)
                                    draw.line([(px, py-5), (px, py+1)], fill=(255, 255, 255, 255), width=2)
                                    draw.ellipse([px-1, py+3, px+1, py+5], fill=(255, 255, 255, 255))
                        except Exception as e:
                            print(f"Error rendering individual drought plot marker: {e}")

            elif layer == "flood":
                # Parse plot_id or user_id to integer if provided
                plot_id_int = None
                if plot_id:
                    try:
                        plot_id_int = parse_plot_id(plot_id)
                    except Exception:
                        pass

                user_id_int = None
                if user_id and plot_id_int is None:
                    try:
                        user_id_int = parse_plot_id(user_id)
                    except Exception:
                        pass

                # Use in-memory plot render cache — no DB query for plot/risk/feature data
                cyclone_icon = GLOBAL_ICONS.get("cyclone")
                wind_icon = GLOBAL_ICONS.get("wind")
                global_wind_deg   = _global_wind_cache["deg"]
                global_wind_speed = _global_wind_cache["speed"]

                for entry in _plots_in_tile(west, south, east, north,
                                            user_id_int=user_id_int, plot_id_int=plot_id_int):
                    plot_geom_wkt = entry["geom_wkt"]
                    centroid_wkt  = entry["centroid_wkt"]
                    risk_score    = entry["flood_risk"]
                    row_wind_deg  = entry["wind_dir"]
                    row_wind_speed = entry["wind_speed"]
                    row_ndmi      = entry["ndmi"]
                    active_risk   = risk_score

                    # 1. Draw the colored agricultural plot boundary polygon if it exists
                    if plot_geom_wkt:
                        try:
                            geom = wkt_loads(plot_geom_wkt)
                            
                            # Color-code based on flood risk score — mirrors riskState.ts thresholds
                            # and alert_engine.py so map colours = notification severity:
                            # >= 0.80: อันตราย / Danger (Red)    rgb(220, 38, 38)  --tas-danger
                            # >= 0.60: เฝ้าระวัง / Warn  (Orange)  rgb(255, 141, 40) --tas-warn
                            #  < 0.60: ปกติดี / OK      (Green)   rgb(64, 171, 104) --tas-ok
                            if active_risk >= 0.80:
                                fill_color = (220, 38, 38, 205)        # --tas-danger (อันตราย)
                                outline_color = (185, 28, 28, 220)
                            elif active_risk >= 0.60:
                                fill_color = (255, 141, 40, 205)       # --tas-warn (เฝ้าระวัง)
                                outline_color = (220, 100, 10, 220)
                            else:
                                fill_color = (64, 171, 104, 205)       # --tas-ok (ปกติดี)
                                outline_color = (45, 135, 78, 220)

                            def draw_plot_poly(g):
                                if g.geom_type == 'Polygon':
                                    ext_coords = [to_pixels(lon, lat) for lon, lat in g.exterior.coords]
                                    if len(ext_coords) >= 3:
                                        draw.polygon(ext_coords, fill=fill_color, outline=outline_color, width=2)
                                    for interior in g.interiors:
                                        int_coords = [to_pixels(lon, lat) for lon, lat in interior.coords]
                                        if len(int_coords) >= 3:
                                            draw.polygon(int_coords, fill=(0, 0, 0, 0), outline=outline_color, width=2)
                                elif g.geom_type == 'MultiPolygon':
                                    for poly in g.geoms:
                                        draw_plot_poly(poly)
                                        
                            draw_plot_poly(geom)
                        except Exception as e:
                            print(f"Error drawing flood colored plot boundary: {e}")

                # 2. Render Cyclone spiral vortex icon & wind vectors centered at the actual cyclone track points only at close zooms (z >= 11)
                if z >= 11:
                    try:
                        cyclone_query = """
                            SELECT 
                                id,
                                cyclone_name,
                                ST_AsText(ST_Transform(ST_Centroid(geometry), 4326)) as centroid,
                                max_wind_speed_kmh,
                                category
                            FROM cyclone_tracks
                            WHERE ST_Intersects(
                                geometry,
                                ST_Transform(
                                    ST_Buffer(
                                        ST_Transform(
                                            ST_MakeEnvelope(%s, %s, %s, %s, 4326),
                                            3857
                                        ),
                                        %s
                                    ),
                                    32647
                                )
                            );
                        """
                        cur.execute(cyclone_query, (west, south, east, north, buffer_meters))
                        cyclones = cur.fetchall()
                        
                        for cyc in cyclones:
                            cyc_id, cyc_name, cyc_centroid_wkt, max_wind, category = cyc
                            if not cyc_centroid_wkt:
                                continue
                                
                            pt = wkt_loads(cyc_centroid_wkt)
                            lon, lat = pt.x, pt.y
                            px, py = to_pixels(lon, lat)
                            
                            # Determine storm coloring: Typhoon level (red) or below (orange)
                            is_typhoon = (max_wind is not None and float(max_wind) >= 118) or (category == "Typhoon")
                            storm_color = (211, 47, 47, 240) if is_typhoon else (245, 124, 0, 240)
                            
                            # A. Render the custom cyclone PNG icon, or a beautiful high-contrast vector spiral fallback
                            if cyclone_icon is not None:
                                iw, ih = cyclone_icon.size
                                img.paste(cyclone_icon, (int(px - iw / 2), int(py - ih / 2)), mask=cyclone_icon)
                            else:
                                # Premium Vector fallback: Draw double-arm spiral vortex in storm red or orange
                                draw.ellipse([px-7, py-7, px+7, py+7], fill=storm_color, outline=(255, 255, 255, 255), width=1)
                                draw.arc([px-14, py-14, px+14, py+14], start=0, end=180, fill=storm_color, width=3)
                                draw.arc([px-14, py-14, px+14, py+14], start=180, end=360, fill=storm_color, width=3)
                                
                            # B. Render the rotated and scaled wind direction vector arrow next to it
                            wind_speed = float(max_wind) if max_wind is not None else global_wind_speed
                            wind_deg = global_wind_deg  # Fallback direction blowing NE
                            
                            arrow_dir = (wind_deg - 180) % 360
                            rad = math.radians(arrow_dir)
                            
                            wx = px + 22
                            wy = py + 14
                            
                            # White base anchor dot
                            draw.ellipse([wx-3, wy-3, wx+3, wy+3], fill=(255, 255, 255, 255))
                            
                            if wind_icon is not None:
                                arr_size = int(max(26, min(42, 18 + wind_speed * 1.0)))
                                resized_w = wind_icon.resize((arr_size, arr_size), Image.LANCZOS)
                                rotated_w = resized_w.rotate((360 - arrow_dir) % 360, resample=Image.BICUBIC, expand=False)
                                img.paste(rotated_w, (int(wx - arr_size / 2), int(wy - arr_size / 2)), mask=rotated_w)
                            else:
                                # Fallback arrow line
                                arr_len = max(26, min(44, 20 + wind_speed * 1.2))
                                ex = wx + arr_len * math.sin(rad)
                                ey = wy - arr_len * math.cos(rad)
                                
                                draw.line([(wx+0.8, wy+0.8), (ex+0.8, ey+0.8)], fill=(0, 0, 0, 180), width=5)
                                draw.line([(wx, wy), (ex, ey)], fill=(0, 225, 255, 255), width=3)
                                
                                hs = 8
                                hlx = ex - hs * math.sin(rad + math.radians(35))
                                hly = ey + hs * math.cos(rad + math.radians(35))
                                hrx = ex - hs * math.sin(rad - math.radians(35))
                                hry = ey + hs * math.cos(rad - math.radians(35))
                                
                                draw.polygon([(ex+0.8, ey+0.8), (hlx+0.8, hly+0.8), (hrx+0.8, hry+0.8)], fill=(0, 0, 0, 180))
                                draw.polygon([(ex, ey), (hlx, hly), (hrx, hry)], fill=(0, 225, 255, 255))
                    except Exception as ce:
                        print(f"Error rendering cyclone tracks: {ce}")

            elif layer == "burn_scar":
                query = """
                    SELECT ST_AsText(ST_Transform(geometry, 4326)), source, area_sqm
                    FROM burn_scars
                    WHERE ST_Intersects(
                        geometry,
                        ST_Transform(ST_MakeEnvelope(%s, %s, %s, %s, 4326), 32647)
                    );
                """
                cur.execute(query, (west, south, east, north))
                rows = cur.fetchall()
                
                for row in rows:
                    wkt, source, area = row
                    geom = wkt_loads(wkt)
                    
                    def draw_geom(g):
                        if g.geom_type == 'Polygon':
                            ext_coords = [to_pixels(lon, lat) for lon, lat in g.exterior.coords]
                            if len(ext_coords) >= 3:
                                draw.polygon(ext_coords, fill=(180, 50, 50, 100), outline=(180, 30, 30, 230))
                            for interior in g.interiors:
                                int_coords = [to_pixels(lon, lat) for lon, lat in interior.coords]
                                if len(int_coords) >= 3:
                                    draw.polygon(int_coords, fill=(0, 0, 0, 0), outline=(180, 30, 30, 230))
                        elif g.geom_type == 'MultiPolygon':
                            for poly in g.geoms:
                                draw_geom(poly)
                                
                    draw_geom(geom)
                    
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            png_bytes = buf.getvalue()
            with _vtile_lock:
                _vector_tile_cache[etag_val] = png_bytes
            return Response(
                content=png_bytes,
                media_type="image/png",
                headers={
                    "Cache-Control": "public, max-age=300, stale-while-revalidate=600",
                    "ETag": etag
                }
            )

        except Exception as e:
            print(f"Error rendering vector tile: {e}")
            return empty_tile()
        finally:
            # Always close cursor and rollback any partial transaction to keep
            # the pooled connection clean — this was the pool exhaustion root cause
            if cur is not None:
                try:
                    cur.close()
                except Exception:
                    pass
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
                db_pool.putconn(conn)

if __name__ == "__main__":
    import uvicorn
    # รันเซิร์ฟเวอร์ (สำหรับรันบน Local ถ้าอยู่บน Cloud จะใช้คำสั่ง uvicorn ผ่าน terminal)
    uvicorn.run("main:app", host="0.0.0.0", port=7860, reload=False)
