# MyCane_BE — Repository Structure

FastAPI backend ("Satellite Team API"). Deployed to Hugging Face Spaces (`heaveneye-geoai-satellite.hf.space`, port 7860).
Pipeline ingestion runs independently in `MyCane_DB`; this repo only reads from Supabase.

---

## Root

| File | Purpose |
|---|---|
| `Dockerfile` | HF Space image build — copies api/ + static assets |
| `docker-compose.yml` | Local dev compose (BE only) |
| `requirements.txt` | Python deps — **byte-identical copy** of `MyCane_DB/requirements.txt` |
| `run_poc.py` | Pipeline entry point — **byte-identical copy** of `MyCane_DB/run_poc.py`; kept for HF Space compat |
| `run_tests.py` | Runs full pytest suite; writes `pytest_result.txt` |
| `README.md` | Project overview |
| `analyze_data_quality.py` | Ad-hoc data quality checks |
| `pytest_result.txt` | Last test run output (gitignored) |

---

## `api/` — FastAPI application root

| File | Purpose |
|---|---|
| `main.py` | ~51-line wiring: `app = FastAPI(lifespan=...)`, middleware, 6× `include_router`. `get_db = get_raw_db` alias for test dependency_overrides. |
| `verify_api.py` | Smoke-test local API |
| `verify_remote.py` | Smoke-test deployed HF Space |

### `api/app/api/` — HTTP routers

| File | Prefix | Purpose |
|---|---|---|
| `auth.py` | `/api/v1/auth` | Register, login (form-encoded), `auth/me`, profile image upload |
| `plots.py` | `/api/v1/plots` | Plot CRUD — create, list, get, update, delete |
| `notifications.py` | `/api/v1/notifications` | List, mark-read, push notification, `run-alert-scan` trigger |
| `features.py` | `/api/v1` | `GET /features/{plot_id}`, `/features`, `/history/{plot_id}`, `/plots` (geo) |
| `hotspots.py` | `/api/v1` | `GET /hotspots` — VIIRS hotspot GeoJSON within bbox |
| `tiles.py` | `/api/v1` | `GET /tiles/{layer}/{z}/{x}/{y}.png` — ETag/304, hourly vector cache, dispatches to tile renderers |

### `api/app/core/` — Infrastructure

| File | Purpose |
|---|---|
| `state.py` | All mutable singletons: `db_pool`, `_plot_render_cache` + lock, `_global_wind_cache`, `GLOBAL_ICONS`, `EMPTY_TILE_BYTES`. Single source of shared state — every module imports as `import app.core.state as state`. |
| `db_pool.py` | `BlockingThreadedConnectionPool` + `get_raw_db()` psycopg2 FastAPI dep. `DATABASE_URL` from `os.environ`. |
| `config.py` | SQLAlchemy `SessionLocal` engine (used by auth/plots/notifications routers). `DATABASE_URL` from env. |
| `security.py` | `create_access_token`, `verify_password`, `get_password_hash`, `get_current_user` (reads `X-App-Authorization` header). |
| `storage.py` | Profile image storage: Supabase Storage when `SUPABASE_KEY` set; base64 data-URI fallback otherwise. |
| `assets.py` | `load_global_assets()` — loads PNG icons into `state.GLOBAL_ICONS`; `_precompute_wind_variants()` — pre-renders wind arrow images at 36 headings. |
| `render_cache.py` | `refresh_plot_render_cache()` — single batch SQL → `state._plot_render_cache`. `_plots_in_tile()` — bbox filter over cache snapshot. `_compute_risk_scores()` — derives `fire_risk`/`flood_risk`/`disease_risk` per plot from CLAUDE.md thresholds. |
| `s3.py` | `download_from_s3`, `check_s3_file_exists` — boto3 helpers reading S3 creds from env. Used at startup to pre-fetch index TIFs. |

### `api/app/models/` — Pydantic domain models

| File | Purpose |
|---|---|
| `domain.py` | `User`, `Plot`, `Notification` DB-layer models |
| `geo.py` | `IndicesFeature`, `HotspotCollection` and other geo response models |

### `api/app/schemas/` — Request/response schemas

| File | Purpose |
|---|---|
| `domain.py` | Request bodies and response shapes for auth/plots/notifications endpoints |

### `api/app/utils/`

| File | Purpose |
|---|---|
| `format.py` | `parse_plot_id`, `format_plot_id`, `build_plot_feature_response`, `safe_float/int`, `normalize_confidence`, `PLOT_FEATURE_SELECT_COLUMNS` |

### `api/app/services/` — Business logic

| File | Purpose |
|---|---|
| `alert_engine.py` | `run_alert_scan()` — PostGIS query finds hotspots within 30m of plots; inserts deduped notifications; dispatches LINE alerts. |
| `dispatch.py` | `dispatch_to_channels(line_user_id, title, message)` — routes alerts to LINE. |
| `line_client.py` | LINE Messaging API push message wrapper. |

#### `api/app/services/tile_render/` — Per-layer tile renderers

| File | Purpose |
|---|---|
| `raster.py` | `_render_index_tile_cached` — `@lru_cache` GeoTIFF raster tile rendering (NDVI/NDMI/NBR). |
| `hotspot.py` | Vector tile: draws plot polygons colored by `fire_risk`; renders VIIRS hotspot pins from DB. |
| `flood.py` | Vector tile: draws plot polygons by `flood_risk`; draws cyclone tracks + wind arrows at z≥11. |
| `disease.py` | Vector tile: draws plot polygons by `disease_risk` (user/plot-scoped, data-leak prevention). |
| `drought.py` | Vector tile: draws plot polygons by NDMI drought signal (user/plot-scoped). |
| `burn_scar.py` | Vector tile: draws burn scar polygons from `burn_scars` table. |
| `__init__.py` | Empty package marker. |

### Top-level `api/app/`

| File | Purpose |
|---|---|
| `lifespan.py` | FastAPI lifespan context manager: DB pool init, spatial index creation, empty tile cache, asset load, render cache warm, S3 TIF pre-fetch, lightweight APScheduler start/stop. |
| `scheduler.py` | `scheduled_cache_and_alerts()` — runs `run_alert_scan()` + `refresh_plot_render_cache()` every 30 min. No pipeline ingestion. |
| `docs/descriptions.py` | Long-form API endpoint descriptions for FastAPI OpenAPI docs. |
| `__init__.py` | Package marker. |

---

## `db/` — Database utilities

| File | Purpose |
|---|---|
| `run_init.py` | Runs `init.sql` against the DB (creates tables, extensions, indexes) |
| `check_schema.py` | Validates current DB schema against expected columns |
| `add_profile_image_url.py` | Migration: adds `profile_image_url` column to `users` |
| `update_old_plots.py` | Backfills `crop`/`address` columns on existing plots |
| `import_shapefiles_to_db.py` | Imports shapefile geometries into `plots` table |
| `check_soil.py` | Ad-hoc: inspect soil/terrain data in DB |

---

## `tests/`

| File | Purpose |
|---|---|
| `conftest.py` | `_FakeThreadedPool` mock, `fake_db` fixture, test client factory. **TestClient used as context manager** (`with TestClient(app) as client:`) so lifespan runs. |
| `test_api_contract.py` | 12 tests covering auth, plot CRUD, notifications, features, tile endpoints, extract_stats integration. Imports `pipeline.zonal_stats` — requires `MyCane_DB` on `PYTHONPATH`. |
| `test_tile_api.py` | Tile endpoint: layer validation, ETag/304 behavior, raster vs vector dispatch. |
| `test_ingestion.py` | Pipeline ingestion smoke tests (needs `MyCane_DB` on `PYTHONPATH`). |
| `test_processing.py` | Raster processing unit tests. |
| `test_sentinel_download.py` | Sentinel-2 download tests. |
| `test_dag_integrity.py` | Airflow DAG import and structure tests. |

---

## Key invariants

- **Two DB access styles**: `get_raw_db` (psycopg2, geo/tile endpoints) and `get_db` (SQLAlchemy, auth/plots/notifications). Same `DATABASE_URL`.
- **Two auth layers**: HF proxy `Authorization: Bearer <HF_TOKEN>` + app JWT in `X-App-Authorization`.
- **`state.py` is the only owner of shared mutable state** — no other module holds globals.
- **`_plot_render_cache` keys per entry**: `user_id`, `geom_wkt`, `centroid_wkt`, `bbox`, `ndvi`, `ndmi`, `humidity_pct`, `wind_dir`, `wind_speed`, `fire_risk`, `flood_risk`, `disease_risk`. Adding new keys → update `render_cache.py`.
- **No pipeline imports at runtime** — `DATABASE_URL` and S3 creds come from env only.
