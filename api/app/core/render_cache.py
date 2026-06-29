from loguru import logger
import app.core.state as state
import app.core.constants as const


def _compute_risk_scores(ndvi, ndmi, nbr, humidity_pct, rain_7d_mm,
                          hotspot_count_24h, hotspot_count_7d, nearest_hotspot_km):
    """
    Derive fire/flood/disease risk scalars from the feature vector using the
    thresholds from CLAUDE.md (mirrored in alert_engine.py and sugarcaneIndices.ts).
    Returns (fire_risk, flood_risk, disease_risk) each in [0.0, 1.0].
    """
    nearest_hs = float(nearest_hotspot_km) if nearest_hotspot_km is not None else const.DEFAULT_MISSING_HOTSPOT_KM
    hs_24h     = int(hotspot_count_24h)    if hotspot_count_24h    is not None else 0
    hum        = float(humidity_pct)       if humidity_pct         is not None else const.DEFAULT_HUMIDITY_PCT
    rain       = float(rain_7d_mm)         if rain_7d_mm           is not None else 0.0

    # Fire — hotspot proximity / recency (CLAUDE.md: danger < 5 km OR count_24h > 3)
    if nearest_hs < const.FIRE_DANGER_KM or hs_24h > const.FIRE_DANGER_COUNT_24H:
        fire_risk = const.RISK_SCORE_DANGER
    elif nearest_hs < const.FIRE_WARN_KM or hs_24h > const.FIRE_WARN_COUNT_24H:
        fire_risk = const.RISK_SCORE_WARN
    else:
        fire_risk = const.RISK_SCORE_OK

    # Disease — humidity + rain accumulation (CLAUDE.md: humidity > 85% AND rain_7d > 15mm)
    if hum > const.DISEASE_DANGER_HUMIDITY and rain > const.DISEASE_DANGER_RAIN_7D:
        disease_risk = const.RISK_SCORE_DANGER
    elif hum > const.DISEASE_WARN_HUMIDITY or rain > const.DISEASE_WARN_RAIN_7D:
        disease_risk = const.RISK_SCORE_DISEASE_WARN
    else:
        disease_risk = const.RISK_SCORE_OK

    # Flood — rain accumulation signal (no explicit CLAUDE.md threshold; derived from rain_7d)
    if rain > const.FLOOD_DANGER_RAIN_7D:
        flood_risk = const.RISK_SCORE_FLOOD_DANGER
    elif rain > const.FLOOD_WARN_RAIN_7D:
        flood_risk = const.RISK_SCORE_FLOOD_WARN
    else:
        flood_risk = const.RISK_SCORE_OK

    return fire_risk, flood_risk, disease_risk


def refresh_plot_render_cache():
    """Rebuild the in-memory plot render cache from a single batch DB query.
    Called at startup and after each sync run.
    Tile rendering reads from this cache instead of hitting the DB per tile."""
    if state.db_pool is None:
        return
    conn = state.db_pool.getconn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                p.id,
                p.user_id,
                ST_AsText(ST_Transform(p.geometry, 4326))              AS geom_wkt,
                ST_AsText(ST_Transform(ST_Centroid(p.geometry), 4326)) AS centroid_wkt,
                ST_XMin(ST_Transform(p.geometry, 4326)),
                ST_YMin(ST_Transform(p.geometry, 4326)),
                ST_XMax(ST_Transform(p.geometry, 4326)),
                ST_YMax(ST_Transform(p.geometry, 4326)),
                pf.ndvi,
                pf.ndmi,
                pf.nbr,
                pf.humidity_pct,
                pf.wind_direction_deg,
                pf.wind_speed_kmh,
                pf.rain_7d_mm,
                pf.hotspot_count_24h,
                pf.hotspot_count_7d,
                pf.nearest_hotspot_km
            FROM plots p
            LEFT JOIN LATERAL (
                SELECT
                    ndvi, ndmi, nbr,
                    humidity_pct, wind_direction_deg, wind_speed_kmh,
                    rain_7d_mm, hotspot_count_24h, hotspot_count_7d, nearest_hotspot_km
                FROM plot_features
                WHERE plot_id = p.id
                ORDER BY timestamp DESC
                LIMIT 1
            ) pf ON TRUE;
        """)
        rows = cur.fetchall()

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
             ndvi, ndmi, nbr,
             humidity_pct, wind_dir, wind_speed,
             rain_7d_mm, hotspot_count_24h, hotspot_count_7d, nearest_hotspot_km) = row

            fire_risk, flood_risk, disease_risk = _compute_risk_scores(
                ndvi, ndmi, nbr, humidity_pct, rain_7d_mm,
                hotspot_count_24h, hotspot_count_7d, nearest_hotspot_km,
            )

            new_cache[pid] = {
                "user_id":      uid,
                "geom_wkt":     geom_wkt,
                "centroid_wkt": centroid_wkt,
                "bbox":         (bbox_west, bbox_south, bbox_east, bbox_north),
                "ndvi":         float(ndvi)              if ndvi              is not None else const.DEFAULT_INDEX_VALUE,
                "ndmi":         float(ndmi)              if ndmi              is not None else const.DEFAULT_INDEX_VALUE,
                "humidity_pct": float(humidity_pct)      if humidity_pct      is not None else const.DEFAULT_HUMIDITY_PCT,
                "wind_dir":     float(wind_dir)          if wind_dir          is not None else const.DEFAULT_WIND_DEG,
                "wind_speed":   float(wind_speed)        if wind_speed        is not None else const.DEFAULT_WIND_SPEED_KMH,
                "fire_risk":    fire_risk,
                "flood_risk":   flood_risk,
                "disease_risk": disease_risk,
            }

        with state._plot_render_cache_lock:
            state._plot_render_cache.clear()
            state._plot_render_cache.update(new_cache)
            if w_row:
                state._global_wind_cache["deg"]   = float(w_row[0])
                state._global_wind_cache["speed"] = float(w_row[1])

        logger.info("✅ Plot render cache refreshed: {} plots.", len(new_cache))
    except Exception as e:
        logger.error("❌ Failed to refresh plot render cache: {}", e)
    finally:
        state.db_pool.putconn(conn)


def _plots_in_tile(west: float, south: float, east: float, north: float,
                   user_id_int=None, plot_id_int=None, pad_deg: float = 0.02) -> list[dict]:
    """Return cached plot entries whose bbox intersects the (padded) tile bbox.
    Falls back to an empty list when the cache has not been populated yet."""
    with state._plot_render_cache_lock:
        snapshot = dict(state._plot_render_cache)
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
