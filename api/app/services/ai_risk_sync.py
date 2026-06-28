"""AI risk score sync — Option A (server-to-server).

Runs after every hourly satellite ingestion pipeline.  For each registered
plot it:
  1. Reads the plot geometry from the DB (stored as SRID 32647 / UTM-47N).
  2. Converts it to WGS-84 GeoJSON and POSTs to DOH /v1/risk-score.
  3. Writes the four per-hazard scores + confidence into plot_risk_scores.

The existing alert_engine.run_alert_scan() is *unchanged*; it reads the
same plot_risk_scores rows and fires notifications whenever a score crosses
the danger/warn threshold.

Configuration (env vars, loaded from pipeline.config or os.environ):
  DOH_API_URL   — e.g. "https://doh.noboru.tech"          (default shown)
  DOH_API_KEY   — x-api-key secret from the AI team

Run order in scheduled_job() (main.py):
  run_pipeline()        ← fresh satellite indices written
  sync_ai_risk_scores() ← this module — calls DOH, writes plot_risk_scores
  run_alert_scan()      ← reads plot_risk_scores, fires notifications
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import psycopg2
import requests
from loguru import logger

# Reuse the pipeline DATABASE_URL (same connection used by alert_engine).
sys.path.append(str(Path(__file__).resolve().parents[3]))
from pipeline.config import DATABASE_URL  # noqa: E402

# ── Config ────────────────────────────────────────────────────────────────────

DOH_API_URL = os.environ.get("DOH_API_URL", "https://doh.noboru.tech")
DOH_API_KEY = os.environ.get("DOH_API_KEY", "")

# Per-request timeout (seconds). DOH dynamic risk-score extraction for new areas takes ~32–35s.
REQUEST_TIMEOUT = 60

# Back-off between per-plot requests so we don't hammer DOH.
REQUEST_DELAY_S = 0.5


# ── Helpers ───────────────────────────────────────────────────────────────────

# Maps satellite land_use_type strings → DOH crop hint.
_LAND_USE_TO_CROP: dict[str, str] = {
    "Paddy Field (นา)": "rice",
    "rice_paddy": "rice",
    "rice": "rice",
    "sugarcane": "sugarcane",
    "corn": "corn",
    "cassava": "cassava",
}


def clamp01(n: float) -> float:
    """Clamp value between 0.0 and 1.0."""
    return max(0.0, min(1.0, float(n)))


def compute_fe_risk_scores(features: dict) -> dict:
    """Compute local high-performance Feature Engineering (FE) physical risk scores.
    Uses identical formulas to the frontend (src/lib/api/ai.ts).
    """
    # 1. 🔥 Physical Fire Risk Calculation
    # Formula: 0.30·HotspotHist + 0.25·BurnScarRecur + 0.20·NDMI + 0.15·Slope + 0.10·LandUse
    burn_scar_recur = float(features.get('burn_scar_recurrence') or 0.0)
    # derive hotspot historical density from burn scar recurrence
    hotspot_hist = max(burn_scar_recur, 0.0) * 0.5
    
    ndmi = float(features.get('ndmi') or 0.5)
    ndmi_fire = clamp01(1.0 - ndmi)
    
    slope = float(features.get('slope_deg') or 5.0)
    slope_factor = clamp01(slope / 45.0)
    
    land_use = str(features.get('land_use_type') or "rice_paddy").lower()
    land_use_val = 0.1
    if "sugarcane" in land_use:
        land_use_val = 0.9
    elif "corn" in land_use:
        land_use_val = 0.8
    elif "cassava" in land_use:
        land_use_val = 0.5
    elif "rice" in land_use:
        land_use_val = 0.3
        
    fire_score = 0.30 * hotspot_hist + 0.25 * burn_scar_recur + 0.20 * ndmi_fire + 0.15 * slope_factor + 0.10 * land_use_val
    fire_score = max(0.05, min(0.95, fire_score))
    
    # 2. 💧 Physical Flood Risk Calculation
    # Formula: 0.40·Rain7d + 0.25·(1/Elevation) + 0.20·(1/RiverDist) + 0.15·SoilWaterCap
    rain_7d = float(features.get('rain_7d_mm') or 0.0)
    rain_7d_factor = min(1.0, rain_7d / 200.0)
    
    elevation = float(features.get('elevation_m') or 100.0)
    elevation_inv = min(1.0, 50.0 / elevation) if elevation > 0 else 1.0
    
    river_dist = float(features.get('river_distance_m') or 1000.0)
    river_dist_inv = min(1.0, 200.0 / river_dist) if river_dist > 0 else 1.0
    
    soil_water_capacity = float(features.get('soil_water_capacity') or 0.45)
    
    flood_score = 0.40 * rain_7d_factor + 0.25 * elevation_inv + 0.20 * river_dist_inv + 0.15 * soil_water_capacity
    flood_score = max(0.05, min(0.95, flood_score))
    
    # 3. 💧 Physical Drought Risk Calculation
    # Formula: 0.45·SPI + 0.30·NDMI + 0.25·RainForecast14d
    spi_30d = float(features.get('spi_30d') or 0.0)
    spi_factor = clamp01((-spi_30d + 2.0) / 4.0)
    
    ndmi_drought = clamp01(1.0 - ndmi)
    
    forecast_rain = float(features.get('rain_forecast_14d_mm') or 50.0)
    rain_forecast_factor = clamp01((100.0 - forecast_rain) / 100.0)
    
    drought_score = 0.45 * spi_factor + 0.30 * ndmi_drought + 0.25 * rain_forecast_factor
    drought_score = max(0.05, min(0.95, drought_score))
    
    # 4. 🐛 Physical Crop Disease Risk Calculation
    disease_score = 0.05
    hum = float(features.get('humidity_pct') or 60.0)
    temp = float(features.get('temp_max_c') or 25.0)
    ndvi = float(features.get('ndvi') or 0.6)
    ndvi_anomaly = clamp01(0.7 - ndvi)
    
    if "rice" in land_use:
        if hum > 85 and 20.0 <= temp <= 30.0:
            disease_score = 0.65 + ndvi_anomaly * 0.30
        elif hum > 75 and 18.0 <= temp <= 32.0:
            disease_score = 0.35 + ndvi_anomaly * 0.25
        else:
            disease_score = 0.10 + ndvi_anomaly * 0.15
    elif "sugarcane" in land_use:
        if hum > 80 and 25.0 <= temp <= 35.0:
            disease_score = 0.70 + ndvi_anomaly * 0.25
        else:
            disease_score = 0.15 + ndvi_anomaly * 0.15
    elif "corn" in land_use:
        if hum > 90 and 18.0 <= temp <= 27.0:
            disease_score = 0.75 + ndvi_anomaly * 0.20
        else:
            disease_score = 0.12 + ndvi_anomaly * 0.15
    elif "cassava" in land_use:
        if temp > 28.0 and hum > 65.0:
            disease_score = 0.60 + ndvi_anomaly * 0.30
        else:
            disease_score = 0.08 + ndvi_anomaly * 0.15
    else:
        if hum > 80 and 20.0 <= temp <= 30.0:
            disease_score = 0.50 + ndvi_anomaly * 0.25
        else:
            disease_score = 0.10 + ndvi_anomaly * 0.10
            
    disease_score = max(0.05, min(0.95, disease_score))
    
    return {
        'fire': round(fire_score, 4),
        'flood': round(flood_score, 4),
        'drought': round(drought_score, 4),
        'disease': round(disease_score, 4)
    }


def _infer_crop(land_use_type: str | None) -> str | None:
    if not land_use_type:
        return None
    # Exact match first, then substring scan.
    crop = _LAND_USE_TO_CROP.get(land_use_type)
    if crop:
        return crop
    lower = land_use_type.lower()
    for key, val in _LAND_USE_TO_CROP.items():
        if key.lower() in lower:
            return val
    return None


def _register_plot_in_doh(plot_id: int, geojson_geometry: dict, crop: str | None) -> str | None:
    """POST to DOH /v1/plots to register the plot and obtain the AI Team's generated UUID.
    
    This ensures that DOH has the plot spatial reference and can score it in under 1 second,
    completely avoiding timeouts (which occur when sending unregistered geometries).
    """
    if not DOH_API_KEY:
        return None
        
    # Ensure geometry Polygon rings are closed to prevent "Invalid polygon geometry" errors (400 Bad Request)
    geom = json.loads(json.dumps(geojson_geometry))
    if geom.get("type") == "Polygon" and isinstance(geom.get("coordinates"), list):
        for ring in geom["coordinates"]:
            if isinstance(ring, list) and len(ring) > 0:
                if ring[0] != ring[-1]:
                    ring.append(ring[0])
    elif geom.get("type") == "MultiPolygon" and isinstance(geom.get("coordinates"), list):
        for poly in geom["coordinates"]:
            if isinstance(poly, list):
                for ring in poly:
                    if isinstance(ring, list) and len(ring) > 0:
                        if ring[0] != ring[-1]:
                            ring.append(ring[0])

    payload = {
        "name": f"PLT-{plot_id:03d}",
        "owner": "Test Farmer",
        "crop": crop or "rice",
        "geometry": geom
    }
    try:
        resp = requests.post(
            f"{DOH_API_URL}/v1/plots",
            json=payload,
            headers={
                "x-api-key": DOH_API_KEY,
                "Content-Type": "application/json",
            },
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code in {200, 201}:
            return resp.json().get("id")
        else:
            logger.warning("DOH plot registration failed status={}: {}", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.warning("DOH plot registration error: {}", exc)
    return None


def _call_doh_risk_score(doh_plot_id: str, geojson_geometry: dict, crop: str | None) -> dict | None:
    """POST to DOH /v1/risk-score with the registered DOH UUID plot_id.

    Returns the raw DOH response dict or None on failure.
    """
    if not DOH_API_KEY:
        logger.warning("DOH_API_KEY not set — skipping AI risk sync")
        return None

    payload = {
        "plot_id": doh_plot_id,
        "geometry": None,  # Passing None leverages pre-registered plot geometry to speed up execution from ~7s to ~0.15s (avoiding timeouts)
        "crop": crop,
        "as_of": None,
    }
    try:
        resp = requests.post(
            f"{DOH_API_URL}/v1/risk-score",
            json=payload,
            headers={
                "x-api-key": DOH_API_KEY,
                "Content-Type": "application/json",
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.Timeout:
        logger.warning("DOH /v1/risk-score timed out after {}s", REQUEST_TIMEOUT)
    except requests.exceptions.HTTPError as exc:
        logger.warning("DOH /v1/risk-score HTTP {}: {}", exc.response.status_code, exc.response.text[:200])
    except Exception as exc:
        logger.warning("DOH /v1/risk-score error: {}", exc)
    return None


def _write_risk_score(cur, plot_id: int, doh: dict) -> None:
    """Upsert one risk-score row into plot_risk_scores."""
    fire    = doh.get("fire",    {}).get("score")
    flood   = doh.get("flood",   {}).get("score")
    drought = doh.get("drought", {}).get("score")
    disease = doh.get("disease", {}).get("score")
    # overall_confidence can be a float 0-1 or a string like "high"/"medium"/"low"
    oc = doh.get("overall_confidence", 0.0)
    if isinstance(oc, str):
        confidence = oc
    elif oc >= 0.65:
        confidence = "high"
    elif oc >= 0.35:
        confidence = "medium"
    else:
        confidence = "low"

    # We do a select-then-upsert (insert or update) to guarantee reusing same row/id for each plot
    cur.execute("SELECT id FROM plot_risk_scores WHERE plot_id = %s LIMIT 1;", (plot_id,))
    row = cur.fetchone()
    if row:
        cur.execute(
            """
            UPDATE plot_risk_scores
            SET evaluated_at = %s,
                fire_risk_score = %s,
                flood_risk_score = %s,
                drought_risk_score = %s,
                disease_risk_score = %s,
                confidence_level = %s
            WHERE plot_id = %s;
            """,
            (datetime.utcnow(), fire, flood, drought, disease, confidence, plot_id),
        )
    else:
        cur.execute(
            """
            INSERT INTO plot_risk_scores
                (plot_id, evaluated_at, fire_risk_score, flood_risk_score,
                 drought_risk_score, disease_risk_score, confidence_level)
            VALUES (%s, %s, %s, %s, %s, %s, %s);
            """,
            (plot_id, datetime.utcnow(), fire, flood, drought, disease, confidence),
        )


# ── Public API ────────────────────────────────────────────────────────────────

def sync_ai_risk_scores() -> dict:
    """Fetch DOH risk scores for every plot and persist to plot_risk_scores.

    Returns a summary dict: {"status": "success"|"error", "synced": N, "failed": N}.
    """
    doh_configured = bool(DOH_API_KEY)
    if not doh_configured:
        logger.warning("🤖 DOH_API_KEY not configured. Bypassing AI API and running in local FE-only mode.")
    else:
        logger.info("🤖 Starting AI risk score sync with DOH pre-screening enabled...")

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    synced = 0
    failed = 0
    api_calls_saved = 0
    api_calls_made = 0

    try:
        # Batch-fetch every plot with its latest features in a single query (replaces N+1 pattern).
        cur.execute(
            """
            SELECT
                p.id,
                p.plot_name,
                ST_AsGeoJSON(ST_Transform(p.geometry, 4326))::json AS geojson,
                pf.land_use_type,
                pf.ndvi, pf.ndmi, pf.nbr, pf.rain_7d_mm, pf.rain_forecast_14d_mm,
                pf.temp_max_c, pf.temp_min_c, pf.humidity_pct, pf.wind_speed_kmh, pf.wind_direction_deg,
                pf.elevation_m, pf.slope_deg, pf.aspect_deg, pf.river_distance_m,
                pf.hotspot_count_24h, pf.hotspot_count_7d, pf.nearest_hotspot_km, pf.burn_scar_recurrence,
                pf.soil_water_capacity, pf.spi_30d, pf.spi_60d, pf.spi_90d, pf.data_freshness_days
            FROM plots p
            LEFT JOIN LATERAL (
                SELECT
                    land_use_type, ndvi, ndmi, nbr, rain_7d_mm, rain_forecast_14d_mm,
                    temp_max_c, temp_min_c, humidity_pct, wind_speed_kmh, wind_direction_deg,
                    elevation_m, slope_deg, aspect_deg, river_distance_m,
                    hotspot_count_24h, hotspot_count_7d, nearest_hotspot_km, burn_scar_recurrence,
                    soil_water_capacity, spi_30d, spi_60d, spi_90d, data_freshness_days
                FROM plot_features
                WHERE plot_id = p.id
                ORDER BY timestamp DESC
                LIMIT 1
            ) pf ON TRUE
            ORDER BY p.id;
            """
        )
        plots = cur.fetchall()
        logger.info("🤖 Found {} plot(s) to score.", len(plots))

        for row in plots:
            (plot_id, plot_name, geojson, land_use_type,
             ndvi, ndmi, nbr, rain_7d_mm, rain_forecast_14d_mm,
             temp_max_c, temp_min_c, humidity_pct, wind_speed_kmh, wind_direction_deg,
             elevation_m, slope_deg, aspect_deg, river_distance_m,
             hotspot_count_24h, hotspot_count_7d, nearest_hotspot_km, burn_scar_recurrence,
             soil_water_capacity, spi_30d, spi_60d, spi_90d, data_freshness_days) = row

            crop = _infer_crop(land_use_type)

            # Build features dict from batch row (ndvi is None when plot has no feature record yet)
            if ndvi is not None:
                features = {
                    'ndvi': ndvi,
                    'ndmi': ndmi,
                    'nbr': nbr,
                    'rain_7d_mm': rain_7d_mm,
                    'rain_forecast_14d_mm': rain_forecast_14d_mm,
                    'temp_max_c': temp_max_c,
                    'temp_min_c': temp_min_c,
                    'humidity_pct': humidity_pct,
                    'wind_speed_kmh': wind_speed_kmh,
                    'wind_direction_deg': wind_direction_deg,
                    'elevation_m': elevation_m,
                    'slope_deg': slope_deg,
                    'aspect_deg': aspect_deg,
                    'river_distance_m': river_distance_m,
                    'hotspot_count_24h': hotspot_count_24h,
                    'hotspot_count_7d': hotspot_count_7d,
                    'nearest_hotspot_km': nearest_hotspot_km,
                    'burn_scar_recurrence': burn_scar_recurrence,
                    'land_use_type': land_use_type,
                    'soil_water_capacity': soil_water_capacity,
                    'spi_30d': spi_30d,
                    'spi_60d': spi_60d,
                    'spi_90d': spi_90d,
                    'data_freshness_days': data_freshness_days,
                }
            else:
                features = {}
                
            # Compute local physical FE risk scores
            fe_scores = compute_fe_risk_scores(features)
            max_fe_score = max(fe_scores.values()) if fe_scores else 0.0
            
            doh = None
            doh_called = False
            
            # Pre-screening: Only call DOH AI API if configured and local risk is medium or high (>= 0.6)
            if doh_configured and max_fe_score >= 0.6:
                logger.debug("  Scoring plot {} ({}) crop={} using DOH AI API (FE score = {:.2f} >= 0.6)", plot_id, plot_name, crop, max_fe_score)
                doh_plot_id = _register_plot_in_doh(plot_id, geojson, crop)
                if doh_plot_id:
                    doh = _call_doh_risk_score(doh_plot_id, geojson, crop)
                    doh_called = True
                    api_calls_made += 1
            else:
                if doh_configured:
                    api_calls_saved += 1
                    logger.info("  ✓ Plot {} ({}) has low local risk ({:.2f}). Bypassed DOH AI API (Saved 1 request).", plot_id, plot_name, max_fe_score)
                else:
                    logger.info("  ✓ Plot {} ({}) scored locally in FE-only mode.", plot_id, plot_name)
            
            # Fallback/Assign: If DOH was not called, is offline, or is not configured, write computed FE scores
            if doh is None:
                if doh_called:
                    logger.warning("  ⚠️ DOH validation failed for Plot {}. Falling back to calculated FE scores.", plot_id)
                    failed += 1
                
                # Format local FE scores matching the DOH API payload format
                fresh_days = features.get('data_freshness_days') or 0
                confidence = "high" if fresh_days <= 5 else "medium" if fresh_days <= 14 else "low"
                
                doh = {
                    "fire": {"score": fe_scores.get('fire', 0.05)},
                    "flood": {"score": fe_scores.get('flood', 0.05)},
                    "drought": {"score": fe_scores.get('drought', 0.05)},
                    "disease": {"score": fe_scores.get('disease', 0.05)},
                    "overall_confidence": confidence
                }

            _write_risk_score(cur, plot_id, doh)
            synced += 1
            
            logger.info(
                "  ✓ Plot {} persisted — fire={:.2f} flood={:.2f} drought={:.2f} disease={:.4f} (confidence: {})",
                plot_id,
                doh.get("fire", {}).get("score", 0),
                doh.get("flood", {}).get("score", 0),
                doh.get("drought", {}).get("score", 0),
                doh.get("disease", {}).get("score", 0),
                doh.get("overall_confidence", "low"),
            )
            
            if doh_called:
                time.sleep(REQUEST_DELAY_S)

        conn.commit()
        logger.info("🤖 AI risk sync complete — {} synced (DOH API calls: {} made, {} saved).", synced, api_calls_made, api_calls_saved)
        return {"status": "success", "synced": synced, "api_calls_made": api_calls_made, "api_calls_saved": api_calls_saved}

    except Exception as exc:
        conn.rollback()
        logger.error("🤖 AI risk sync crashed: {}", exc)
        return {"status": "error", "detail": str(exc), "synced": synced, "failed": failed}
    finally:
        cur.close()
        conn.close()
