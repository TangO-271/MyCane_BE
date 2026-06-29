# Refactor Plan — `api/main.py`

**Goal:** split the 1667-line `api/main.py` into focused modules, leaving `main.py` as thin app wiring only. Pure structural move — **behavior must stay byte-identical**.

## Why

One file currently holds six unrelated concerns:

| Concern | Lines |
|---|---|
| `BlockingThreadedConnectionPool` class | 27–90 |
| Scheduler jobs (`scheduled_phase1/phase2/startup/job`) | 92–140 |
| Asset loading + wind-icon precompute | 142–260 |
| `refresh_plot_render_cache`, `_plots_in_tile` | 261–359 |
| `lifespan` + health | 360–449 |
| Helpers + Pydantic models | 464–642 |
| `get_db` dependency | 646–672 |
| Feature / plot / hotspot endpoints | 680–865 |
| `_render_index_tile_cached` (raster) | 866–941 |
| **`get_tile`** — raster + 5 vector layers inline | **942–1667 (~700 lines)** |

Worst offender: `get_tile` renders raster (`ndvi/ndmi/nbr`) **and** five vector layers (`hotspot`, `disease`, `drought`, `flood`, `burn_scar`) inline in a single function.

## Hard constraints

- **Do not simplify the performance machinery** (per root `CLAUDE.md`). `_plot_render_cache`, `_global_wind_cache`, `_precompute_wind_variants`, `@lru_cache` raster tiles, hourly-ETag vector cache, GZip middleware, startup spatial indexes — all intentional. Extract, never rewrite.
- Keep the cross-repo imports `from pipeline.config import DATABASE_URL` and `from run_poc import main`.
- WGS84 in/out, UTM 32647 internal — unchanged.
- Tests must pass after **every** step.

## Target layout (under `api/`)

```
app/core/
  state.py        # singletons: db_pool, _plot_render_cache(+lock),
                  #   _global_wind_cache, GLOBAL_ICONS, EMPTY_TILE_BYTES
  db_pool.py      # BlockingThreadedConnectionPool (L27-90) + get_db dep (L646)
  assets.py       # load_global_assets, _precompute_wind_variants (L142-260)
  render_cache.py # refresh_plot_render_cache, _plots_in_tile (L261-359)
app/scheduler.py  # scheduled_phase1/phase2/startup/job (L92-140) + scheduler build
app/lifespan.py   # lifespan (L360-449)
app/models/geo.py # IndicesFeature..HotspotCollection (L543-614)
app/utils/format.py # parse/format_plot_id, safe_float/int, normalize_confidence,
                    #   build_plot_feature_response, SELECT consts (L464-642)
app/api/
  features.py     # /features, /features/{id}, /history, /plots (L680-814)
  hotspots.py     # /hotspots (L815-865)
  tiles.py        # get_tile dispatcher (thin) + route (L942)
app/services/tile_render/
  raster.py       # _render_index_tile_cached (L866-941)
  hotspot.py      # layer == "hotspot" block
  disease.py      # layer == "disease" block
  drought.py      # layer == "drought" block
  flood.py        # layer == "flood" block
  burn_scar.py    # layer == "burn_scar" block
  common.py       # etag calc, tile bbox, buffer math, empty_tile helper
main.py           # imports, app = FastAPI, middleware, include_router,
                  #   lifespan wiring ONLY
```

## Key risk — import cycles

Shared mutable globals (`db_pool`, `_plot_render_cache`, `_global_wind_cache`, `GLOBAL_ICONS`) are read by both scheduler/lifespan **and** the tile services. To avoid cycles: **all mutable singletons live in `app/core/state.py`**. Every other module imports them from there. No module owns shared state except `state.py`.

## Sequence (tests green after each step)

1. **Pure leaf** — `utils/format.py` + `models/geo.py`. Zero internal deps. Lowest risk.
2. **State + pool** — `state.py`, `db_pool.py`, `get_db`.
3. **Assets + cache** — `assets.py`, `render_cache.py` (read from `state`).
4. **Scheduler + lifespan** — wire jobs/lifespan to new modules.
5. **Simple routers** — `features.py`, `hotspots.py`.
6. **Tile split (biggest)** — extract `raster.py` first, then carve each `layer ==` block out of `get_tile` into `tile_render/<layer>.py`. `get_tile` becomes a ~40-line dispatcher.
7. **Slim `main.py`** — app wiring only.

## Test gate (run after each step)

```powershell
& "C:\Users\tango\miniconda3\envs\mycane\python.exe" -m pytest tests/test_api_contract.py tests/test_tile_api.py -v
```

`TestClient` must be used as a context manager (`with TestClient(app) as client:`) so Starlette runs the lifespan and `db_pool` initializes.

## Effort

Seven steps; step 6 is the bulk. No logic change → low correctness risk provided the test gate passes at every step.
