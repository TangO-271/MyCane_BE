# Performance: tile → plot map logic

Research into how per-plot map values (NDVI / NDMI / humidity / nearest hotspot)
are computed today, where the cost lives, and a set-based PostGIS rework that is
both faster and more correct.

**Scope:** spans two repos — the heavy compute is in `MyCane_DB` (the pipeline,
`extract_stats.py`), the serving layer is in `MyCane_BE` (`render_cache.py` +
tile renderers).

---

## 1. Current architecture

Two compute sites produce/consume per-plot values:

| Site | File | What it does | How |
|---|---|---|---|
| **Pipeline** | `MyCane_DB/pipeline/zonal_stats/extract_stats.py` | per-plot NDVI/NDMI/NBR zonal mean + weather + nearest hotspot → `plot_features` | per-plot Python loop, windowed raster read, per-plot DB connect |
| **BE** | `MyCane_BE/api/app/core/render_cache.py` | one batch SQL → in-memory `_plot_render_cache`; tiles read cache, never query raster per tile | single `LEFT JOIN LATERAL` at startup + post-sync ✅ already optimal |

Key fact: **tile rendering already does NOT hit rasters or do per-plot DB queries.**
It serves precomputed `plot_features` from an in-memory cache rebuilt hourly.
So the optimization target is the **pipeline**, not the tile endpoints.

All geometry is stored in **EPSG:32647 (UTM 47N, meters)**. GiST indexes exist on
`plots.geometry` (created at BE startup, `lifespan.py:35`), `hotspots.geometry`,
and `burn_scars.geometry` (`db/init.sql`). This makes set-based spatial SQL the
natural fast path — it is currently underused.

---

## 2. Bottlenecks

### 2.1 Zonal stats — raster reopened per plot

`extract_stats.main()` is invoked **once per plot** from
`run_poc.py:process_single_plot` (ThreadPoolExecutor, `max_workers=10`). Each call:

1. opens a psycopg2 connection just to fetch one plot's geometry
2. loops `scene_dirs`, opens each `*_NDVI.tif`, does a windowed read +
   `rasterio.features.geometry_mask`, repeats for NDMI/NBR
3. opens a **second** psycopg2 connection to INSERT one row

For N = 150 plots that is roughly:

- ~300 short-lived DB connections (Supabase pooler round-trips)
- 150 × 3 index rasters reopened — each raster read N times instead of once
- scene-matching loop reopens additional TIFs until one covers the plot

The raster is the same file for every plot in a tile; reading it N times is pure waste.

### 2.2 Nearest hotspot — incorrect AND redundant

`pipeline/ingestion/fetch_viirs.py:analyze_hotspots()` computes distance with a
pandas Haversine approximation against `plot_bbox = AOI_BBOX`:

```python
plot_center_lat = (plot_bbox[1] + plot_bbox[3]) / 2   # AOI center, not the plot
plot_center_lon = (plot_bbox[0] + plot_bbox[2]) / 2
```

Consequences:

- distance is measured from the **center of the whole AOI**, so every plot gets
  the **same** `nearest_hotspot_km` — the per-plot value is meaningless
- recomputed in Python for each plot (redundant)
- ignores the GiST-indexed `hotspots` table entirely

Per the shared threshold contract (`CLAUDE.md`), `nearest_hotspot_km` drives the
fire danger/warn bands (`< 5 km` danger, `< 20 km` warn) — so this directly
corrupts fire risk.

### 2.3 `_plots_in_tile` linear scan

`render_cache.py:_plots_in_tile` snapshots the whole cache and bbox-tests every
plot in Python per tile. Fine at hundreds of plots; revisit with an in-memory
`shapely.strtree.STRtree` if plot count grows to thousands.

---

## 3. Recommendations (ranked by ROI)

### A. Nearest hotspot + counts → one PostGIS KNN query  ⭐ do first

Biggest correctness **and** perf win, smallest change. Geometry is in meters and
both tables are GiST-indexed, so this is exact and index-accelerated. Replaces the
entire `analyze_hotspots` Haversine path with a single all-plots query:

```sql
SELECT p.id,
       COALESCE(c.cnt_24h, 0)       AS hotspot_count_24h,
       COALESCE(c.cnt_7d,  0)       AS hotspot_count_7d,
       COALESCE(nn.dist_km, 999.0)  AS nearest_hotspot_km
FROM plots p
LEFT JOIN LATERAL (                              -- true nearest via <-> KNN
    SELECT ST_Distance(p.geometry, h.geometry) / 1000.0 AS dist_km
    FROM hotspots h
    ORDER BY p.geometry <-> h.geometry
    LIMIT 1
) nn ON TRUE
LEFT JOIN LATERAL (                              -- proximity counts by recency
    SELECT
        COUNT(*) FILTER (WHERE acq_time > now() - interval '24 hours') AS cnt_24h,
        COUNT(*) FILTER (WHERE acq_time > now() - interval '7 days')   AS cnt_7d
    FROM hotspots h
    WHERE ST_DWithin(p.geometry, h.geometry, 20000)   -- 20 km warn radius
) c ON TRUE;
```

One round trip, all plots, accurate meters. `analyze_hotspots()` and the per-plot
FIRMS analysis step are deleted.

### B. Zonal stats → read each raster once, vectorize over all polygons  ⭐ do second

Swap the per-plot `geometry_mask` for `exactextract` (C++ core, area-weighted
partial-pixel coverage). Read each index raster **once**, get the mean for **every**
plot in a single pass:

```python
from exactextract import exact_extract
# plots_gdf: all plot polygons reprojected to the raster CRS, loaded once
ndvi = exact_extract(ndvi_tif, plots_gdf, ["mean"])   # mean per plot, one read
ndmi = exact_extract(ndmi_tif, plots_gdf, ["mean"])
nbr  = exact_extract(nbr_tif,  plots_gdf, ["mean"])
```

Then one batched upsert with `psycopg2.extras.execute_values` (1 connection, not
~300). `rasterstats.zonal_stats` is an acceptable pure-Python fallback but slower
and not partial-pixel weighted.

### C. Alternative to B — PostGIS raster (in-DB zonal stats)

`CREATE EXTENSION postgis_raster`, load index TIFs via `raster2pgsql`, then:

```sql
SELECT p.id, (ST_SummaryStats(ST_Clip(r.rast, p.geometry))).mean AS ndvi
FROM plots p
JOIN ndvi_rast r ON ST_Intersects(r.rast, p.geometry);
```

Fully set-based in-DB, GiST-joined, zero Python raster I/O. Trade-off: must load
rasters into the DB every cycle and add the extension. Prefer **B** unless you want
the file-I/O fully gone.

### D. Restructure the pipeline as set-based

Fold A + B together: fetch rasters and hotspots once → compute **all** plots in two
SQL queries (A) plus one vectorized raster pass (B) → one batch upsert. This
removes `ThreadPoolExecutor(max_workers=10)` and the per-plot file shuffle entirely.
The pipeline goes from O(N) connections + O(N) raster opens to O(1) of each.

---

## 4. Separate bug surfaced during research (not perf)

`render_cache.refresh_plot_render_cache()` populates each cache entry with
`ndvi, ndmi, humidity_pct, wind_dir, wind_speed` only — but the tile renderers read
keys that are **never set**:

- `app/services/tile_render/hotspot.py` → `entry["fire_risk"]`
- `app/services/tile_render/flood.py`   → `entry["flood_risk"]`
- `app/services/tile_render/disease.py` → `entry["disease_risk"]`

Any plot that falls inside a `hotspot` / `flood` / `disease` tile raises `KeyError`,
caught by the renderer's `try/except` and silently dropped — so risk-colored plot
polygons never draw on those layers. Pre-existing; carried through the `main.py`
refactor unchanged (pure structural move).

**Fix direction:** `refresh_plot_render_cache` should derive `fire_risk`,
`flood_risk`, `disease_risk` per plot using the same thresholds as
`app/services/alert_engine.py` (single source of truth), and store them in the cache
entry. Then the renderers read populated keys.

---

## 5. Suggested order of work

1. **A** — nearest-hotspot KNN query in the pipeline (correctness + perf, isolated).
2. **Fix §4** — risk keys in `render_cache` (unblocks colored plots on 3 layers).
3. **B** — `exactextract` zonal stats + batched upsert.
4. **D** — collapse the per-plot loop into set-based stages once A/B land.
