# MyCane_BE — Repository Structure

FastAPI backend ("Satellite Team API"). Deployed to Hugging Face Spaces (`heaveneye-geoai-satellite.hf.space`, port 7860).
Pipeline ingestion runs independently in `MyCane_DB`; this repo only reads/serves from Supabase.

---

## Root

| File | Purpose |
|---|---|
| `api/main.py` | ~51-line wiring: `app = FastAPI(lifespan=...)`, GZip middleware, 6× `include_router`. `get_db = get_raw_db` alias for test `dependency_overrides`. |
| `Dockerfile` | HF Space image — copies `api/` + static assets |
| `docker-compose.yml` | Local dev compose (BE only) |
| `requirements.txt` | Python deps — **byte-identical copy** of `MyCane_DB/requirements.txt` |
| `run_poc.py` | Pipeline entry point — **byte-identical copy** of `MyCane_DB/run_poc.py`; kept for HF Space compat |
| `run_tests.py` | Runs full pytest suite, writes `pytest_result.txt` |
| `analyze_data_quality.py` | Ad-hoc data quality checks |

---

## `api/app/` — Application package

### Startup & scheduling

| File | Purpose |
|---|---|
| `lifespan.py` | FastAPI lifespan: DB pool init + spatial indexes, empty tile cache, asset load, render cache warm, S3 TIF pre-fetch, APScheduler start/stop. |
| `scheduler.py` | `scheduled_cache_and_alerts()` — `run_alert_scan()` + `refresh_plot_render_cache()` every 30 min. No pipeline ingestion. |

### `api/` — HTTP routers

| File | Prefix | Purpose |
|---|---|---|
| `app/api/auth.py` | `/api/v1/auth` | Register, login (form-encoded), `auth/me`, profile image upload |
| `app/api/plots.py` | `/api/v1/plots` | Plot CRUD — create, list, get, update, delete |
| `app/api/notifications.py` | `/api/v1/notifications` | List, mark-read, push, `run-alert-scan` trigger |
| `app/api/features.py` | `/api/v1` | `GET /features/{plot_id}`, `/features`, `/history/{plot_id}`, `/plots` (geo) |
| `app/api/hotspots.py` | `/api/v1` | `GET /hotspots` — VIIRS hotspot GeoJSON within bbox |
| `app/api/tiles.py` | `/api/v1` | `GET /tiles/{layer}/{z}/{x}/{y}.png` — ETag/304, hourly vector cache, dispatches to renderers |

### `app/core/` — Infrastructure

| File | Purpose |
|---|---|
| `state.py` | All mutable singletons: `db_pool`, `_plot_render_cache` + lock, `_global_wind_cache`, `GLOBAL_ICONS`, `EMPTY_TILE_BYTES`. Imported everywhere as `import app.core.state as state`. |
| `db_pool.py` | `BlockingThreadedConnectionPool` + `get_raw_db()` psycopg2 dep. `DATABASE_URL` from `os.environ`. |
| `config.py` | SQLAlchemy `SessionLocal` engine for auth/plots/notifications routers. `DATABASE_URL` from env. |
| `security.py` | `create_access_token`, `verify_password`, `get_password_hash`, `get_current_user` (reads `X-App-Authorization`). |
| `storage.py` | Profile image: Supabase Storage when `SUPABASE_KEY` set; base64 data-URI fallback otherwise. |
| `assets.py` | `load_global_assets()` → `state.GLOBAL_ICONS`; `_precompute_wind_variants()` → 36-heading wind arrow cache. |
| `render_cache.py` | `refresh_plot_render_cache()` — batch SQL → `state._plot_render_cache`. `_plots_in_tile()` — bbox filter. `_compute_risk_scores()` — derives `fire_risk`/`flood_risk`/`disease_risk` from CLAUDE.md thresholds. |
| `s3.py` | `download_from_s3`, `check_s3_file_exists` — boto3, S3 creds from env. Used at startup to pre-fetch index TIFs. |

### `app/services/` — Business logic

| File | Purpose |
|---|---|
| `alert_engine.py` | `run_alert_scan()` — finds hotspots within 30m of plots, inserts deduped notifications, dispatches LINE alerts. |
| `dispatch.py` | `dispatch_to_channels(line_user_id, title, message)` — routes to LINE. |
| `line_client.py` | LINE Messaging API push wrapper. |

#### `app/services/tile_render/` — Per-layer renderers

| File | Layer | Notes |
|---|---|---|
| `raster.py` | `ndvi` `ndmi` `nbr` | `@lru_cache` GeoTIFF raster rendering |
| `hotspot.py` | `hotspot` | Plot polygons by `fire_risk`; VIIRS pins from DB |
| `flood.py` | `flood` | Plot polygons by `flood_risk`; cyclone tracks + wind arrows at z≥11 |
| `disease.py` | `disease` | Plot polygons by `disease_risk` — user/plot-scoped (data-leak prevention) |
| `drought.py` | `drought` | Plot polygons by NDMI signal — user/plot-scoped |
| `burn_scar.py` | `burn_scar` | Burn scar polygons from `burn_scars` table |

### `app/models/` & `app/schemas/`

| File | Purpose |
|---|---|
| `models/domain.py` | DB-layer models: `User`, `Plot`, `Notification` |
| `models/geo.py` | Geo response models: `IndicesFeature`, `HotspotCollection` |
| `schemas/domain.py` | Request bodies + response shapes for auth/plots/notifications |
| `utils/format.py` | `parse_plot_id`, `format_plot_id`, `build_plot_feature_response`, `PLOT_FEATURE_SELECT_COLUMNS` |
| `docs/descriptions.py` | Long-form OpenAPI endpoint descriptions |

---

## `db/` — Database utilities

| File | Purpose |
|---|---|
| `run_init.py` | Creates tables, PostGIS extensions, GiST indexes |
| `check_schema.py` | Validates schema against expected columns |
| `add_profile_image_url.py` | Migration: `profile_image_url` on `users` |
| `update_old_plots.py` | Backfills `crop`/`address` on existing plots |
| `import_shapefiles_to_db.py` | Imports shapefile geometries → `plots` |
| `check_soil.py` | Ad-hoc terrain/soil data inspection |

---

## `tests/`

| File | Purpose |
|---|---|
| `conftest.py` | `_FakeThreadedPool` mock, `fake_db` fixture. **TestClient must be used as context manager** so lifespan runs and `db_pool` initializes. |
| `test_api_contract.py` | 12 tests: auth, plot CRUD, notifications, features, tiles, extract_stats. Imports `pipeline.zonal_stats` → requires `MyCane_DB` on `PYTHONPATH`. |
| `test_tile_api.py` | Layer validation, ETag/304, raster vs vector dispatch. |
| `test_ingestion.py` | Pipeline ingestion smoke tests (needs `MyCane_DB` on `PYTHONPATH`). |
| `test_processing.py` | Raster processing unit tests. |
| `test_sentinel_download.py` | Sentinel-2 download tests. |
| `test_dag_integrity.py` | Airflow DAG import + structure tests. |

Run: `PYTHONPATH=C:\Dev\MyCane\MyCane_DB;C:\Dev\MyCane\MyCane_BE\api pytest tests/test_api_contract.py tests/test_tile_api.py -v`

---

## Key invariants

- **Two DB access styles**: `get_raw_db` psycopg2 (geo/tile endpoints) and `get_db` SQLAlchemy (auth/plots/notifications). Same `DATABASE_URL`.
- **Two auth layers**: HF proxy `Authorization: Bearer <HF_TOKEN>` + app JWT in `X-App-Authorization` (NOT `Authorization`).
- **`state.py` owns all shared mutable state** — no module-level globals elsewhere.
- **`_plot_render_cache` entry keys**: `user_id`, `geom_wkt`, `centroid_wkt`, `bbox`, `ndvi`, `ndmi`, `humidity_pct`, `wind_dir`, `wind_speed`, `fire_risk`, `flood_risk`, `disease_risk`. New keys → update `render_cache.py`.
- **No pipeline imports at runtime** — `DATABASE_URL` and S3 creds from env only.
