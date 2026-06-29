from loguru import logger
import app.core.state as state


def refresh_plot_render_cache():
    """Rebuild the in-memory plot render cache from a single batch DB query.
    Called at startup and after each sync_ai_risk_scores() run.
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
