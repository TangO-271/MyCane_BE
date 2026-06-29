import os
from pathlib import Path
from PIL import Image
from loguru import logger
import app.core.state as state
import app.core.constants as const

# api/static/ resolved relative to this file (api/app/core/assets.py → up 3 levels → api/)
_STATIC_DIR = Path(__file__).parent.parent.parent / "static"


def _load_icon(name, candidate_paths, target_h=None):
    """Load the first existing candidate, crop to its bbox, optionally resize to
    target_h (preserving aspect), and cache it under GLOBAL_ICONS[name].

    target_h=None stores the cropped icon un-resized (used for the wind icon,
    which is resized per-render).
    """
    try:
        for path in candidate_paths:
            if os.path.exists(path):
                raw_icon = Image.open(path).convert("RGBA")
                bbox = raw_icon.getbbox()
                if bbox:
                    raw_icon = raw_icon.crop(bbox)
                if target_h is not None:
                    w, h = raw_icon.size
                    target_w = max(4, int(target_h * (w / h)))
                    raw_icon = raw_icon.resize((target_w, target_h), Image.LANCZOS)
                state.GLOBAL_ICONS[name] = raw_icon
                logger.info(f"✅ Cached {name} icon from {path}")
                break
    except Exception as e:
        logger.error(f"Error caching {name} icon: {e}")


def load_global_assets():
    _load_icon("fire", [str(_STATIC_DIR / "fire_icon.png")], const.ICON_HEIGHT_STD)
    _load_icon("wind", [str(_STATIC_DIR / "wind_icon.png")])  # un-resized; scaled per-render
    _load_icon("disease", [str(_STATIC_DIR / "disease_icon.png")], const.ICON_HEIGHT_STD)
    _load_icon("water", [str(_STATIC_DIR / "water_icon.png")], const.ICON_HEIGHT_STD)
    _load_icon("cyclone", [
        str(_STATIC_DIR / "cyclone_icon.png"),
        str(_STATIC_DIR / "storm_icon.png"),
    ], const.ICON_HEIGHT_CYCLONE)


def _precompute_wind_variants():
    """Pre-render wind icon at all (size × angle) combinations used during tile rendering.
    Called once after load_global_assets(); eliminates LANCZOS + BICUBIC per hotspot."""
    wind = state.GLOBAL_ICONS.get("wind")
    if wind is None:
        return
    state.WIND_ICON_VARIANTS.clear()
    for px_size in range(26, 44, 2):   # 26, 28, 30, ..., 42
        base = wind.resize((px_size, px_size), Image.LANCZOS)
        for slot in range(16):          # 0 → 0°, 1 → 22.5°, ..., 15 → 337.5°
            deg = slot * 22.5
            state.WIND_ICON_VARIANTS[(px_size, slot)] = base.rotate(
                (360 - deg) % 360, resample=Image.BICUBIC, expand=False
            )
    logger.info(f"✅ Pre-computed {len(state.WIND_ICON_VARIANTS)} wind icon variants (size×angle).")
