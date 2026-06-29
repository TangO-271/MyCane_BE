"""
Mutable singleton state for the API process.
All modules import from here — no module owns state except this one.
"""
import threading

# psycopg2 connection pool (initialized in lifespan)
db_pool = None

# Static PNG icons loaded at startup; keys: "fire", "wind", "disease", "water", "cyclone"
GLOBAL_ICONS: dict = {
    "fire": None,
    "wind": None,
    "disease": None,
    "water": None,
    "cyclone": None,
}

# Transparent 256×256 PNG bytes cached at startup
EMPTY_TILE_BYTES: bytes | None = None

# Pre-computed wind icon variants: (px_size, angle_slot) → PIL.Image
# Sizes 26–42px (even steps), 16 angle slots × 22.5°
WIND_ICON_VARIANTS: dict = {}

# In-process vector tile cache keyed by ETag (layer+coords+hour)
_vector_tile_cache: dict = {}
_vtile_lock = threading.Lock()
_vtile_cache_hour: str = ""

# In-memory plot render cache rebuilt after each sync
_plot_render_cache: dict = {}
_plot_render_cache_lock = threading.Lock()

# Latest wind reading across all plots (fallback for hotspot tiles)
_global_wind_cache: dict = {"deg": 225.0, "speed": 12.0}
