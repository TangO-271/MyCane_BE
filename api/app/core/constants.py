"""Shared magic numbers and thresholds for the BE.

Single source of truth for risk thresholds (mirrored from CLAUDE.md),
render-cache fallbacks, tile colours, HTTP cache headers, and icon sizing.
Previously these literals were copy-pasted across render_cache.py, tiles.py,
and the tile_render/* renderers.
"""

# ── Risk scoring thresholds (CLAUDE.md sugarcane contract) ───────────────────
# Fire — hotspot proximity / recency
FIRE_DANGER_KM = 5.0
FIRE_WARN_KM = 20.0
FIRE_DANGER_COUNT_24H = 3      # count_24h > 3 → danger
FIRE_WARN_COUNT_24H = 0        # count_24h > 0 → warn
# Disease — humidity + rain accumulation
DISEASE_DANGER_HUMIDITY = 85.0
DISEASE_DANGER_RAIN_7D = 15.0
DISEASE_WARN_HUMIDITY = 70.0
DISEASE_WARN_RAIN_7D = 10.0
# Flood — rain accumulation signal (no explicit CLAUDE.md threshold)
FLOOD_DANGER_RAIN_7D = 40.0
FLOOD_WARN_RAIN_7D = 20.0

# Derived risk scalars written into the render cache (each in [0.0, 1.0])
RISK_SCORE_DANGER = 0.90
RISK_SCORE_WARN = 0.70
RISK_SCORE_DISEASE_WARN = 0.55
RISK_SCORE_FLOOD_DANGER = 0.85
RISK_SCORE_FLOOD_WARN = 0.65
RISK_SCORE_OK = 0.20

# ── Render-cache fallbacks for missing feature values ────────────────────────
DEFAULT_MISSING_HOTSPOT_KM = 999.0
DEFAULT_HUMIDITY_PCT = 50.0
DEFAULT_WIND_DEG = 225.0
DEFAULT_WIND_SPEED_KMH = 12.0
DEFAULT_INDEX_VALUE = 0.5      # ndvi / ndmi fallback

# ── Tile risk colours (hotspot + flood plot fills) ───────────────────────────
# Map colours = notification severity (mirrors riskState.ts / alert_engine.py):
#   >= DANGER cutoff → อันตราย (Red)   ·  >= WARN cutoff → เฝ้าระวัง (Orange)  ·  else ปกติดี (Green)
RISK_COLOR_DANGER_CUTOFF = 0.80
RISK_COLOR_WARN_CUTOFF = 0.60
RISK_FILL_DANGER = (220, 38, 38, 205)
RISK_OUTLINE_DANGER = (185, 28, 28, 220)
RISK_FILL_WARN = (255, 141, 40, 205)
RISK_OUTLINE_WARN = (220, 100, 10, 220)
RISK_FILL_OK = (64, 171, 104, 205)
RISK_OUTLINE_OK = (45, 135, 78, 220)

# ── HTTP cache headers (tile responses) ──────────────────────────────────────
CACHE_TILE_SHORT = "public, max-age=300, stale-while-revalidate=600"
CACHE_TILE_LONG = "public, max-age=86400, stale-while-revalidate=172800"

# ── Web Mercator (EPSG:3857) projection half-extent (metres) ─────────────────
WEB_MERCATOR_HALF = 20037508.34

# ── Wind icon sizing (px) — used by hotspot + flood wind vectors ─────────────
WIND_ICON_MIN = 26
WIND_ICON_MAX = 42
WIND_ICON_BASE = 18
WIND_ICON_SCALE = 1.0

# ── Icon target heights (px) — used by assets.load_global_assets ─────────────
ICON_HEIGHT_STD = 36
ICON_HEIGHT_CYCLONE = 32

# ── Notification cleanup (stale purge — notification_cleanup.py) ──────────────
NOTIF_MAX_AGE_DAYS = 30        # hard cap: delete ANY notification older than this
NOTIF_READ_AGE_DAYS = 7        # delete READ notifications older than this
NOTIF_ORPHAN_AGE_DAYS = 1      # plot-scoped rows whose plot was deleted (plot_id NULL)
DROUGHT_SPI_30D = -1.0         # SPI-30d >= this → drought condition resolved (CLAUDE.md: < -1.0 confirms drought)
# Plot-scoped hazards whose alert is tied to a live per-plot condition. `system`
# alerts are broadcast/manual and are only purged by age, never by condition.
PLOT_SCOPED_HAZARDS = ("fire", "disease", "flood", "drought")
