import os
from pathlib import Path
from PIL import Image
from loguru import logger
import app.core.state as state

# api/static/ resolved relative to this file (api/app/core/assets.py → up 3 levels → api/)
_STATIC_DIR = Path(__file__).parent.parent.parent / "static"


def load_global_assets():
    # 1. Fire Icon
    try:
        icon_paths = [str(_STATIC_DIR / "fire_icon.png")]
        for path in icon_paths:
            if os.path.exists(path):
                raw_icon = Image.open(path).convert("RGBA")
                bbox = raw_icon.getbbox()
                if bbox:
                    raw_icon = raw_icon.crop(bbox)
                w, h = raw_icon.size
                target_h = 36
                target_w = max(4, int(target_h * (w / h)))
                state.GLOBAL_ICONS["fire"] = raw_icon.resize((target_w, target_h), Image.LANCZOS)
                logger.info(f"✅ Cached fire icon from {path}")
                break
    except Exception as e:
        logger.error(f"Error caching fire icon: {e}")

    # 2. Wind Icon
    try:
        w_icon_paths = [str(_STATIC_DIR / "wind_icon.png")]
        for path in w_icon_paths:
            if os.path.exists(path):
                raw_w_icon = Image.open(path).convert("RGBA")
                w_bbox = raw_w_icon.getbbox()
                if w_bbox:
                    raw_w_icon = raw_w_icon.crop(w_bbox)
                state.GLOBAL_ICONS["wind"] = raw_w_icon
                logger.info(f"✅ Cached wind icon from {path}")
                break
    except Exception as e:
        logger.error(f"Error caching wind icon: {e}")

    # 3. Disease Icon
    try:
        icon_paths = [str(_STATIC_DIR / "disease_icon.png")]
        for path in icon_paths:
            if os.path.exists(path):
                raw_icon = Image.open(path).convert("RGBA")
                bbox = raw_icon.getbbox()
                if bbox:
                    raw_icon = raw_icon.crop(bbox)
                w, h = raw_icon.size
                target_h = 36
                target_w = max(4, int(target_h * (w / h)))
                state.GLOBAL_ICONS["disease"] = raw_icon.resize((target_w, target_h), Image.LANCZOS)
                logger.info(f"✅ Cached disease icon from {path}")
                break
    except Exception as e:
        logger.error(f"Error caching disease icon: {e}")

    # 4. Water Icon
    try:
        icon_paths = [str(_STATIC_DIR / "water_icon.png")]
        for path in icon_paths:
            if os.path.exists(path):
                raw_icon = Image.open(path).convert("RGBA")
                bbox = raw_icon.getbbox()
                if bbox:
                    raw_icon = raw_icon.crop(bbox)
                w, h = raw_icon.size
                target_h = 36
                target_w = max(4, int(target_h * (w / h)))
                state.GLOBAL_ICONS["water"] = raw_icon.resize((target_w, target_h), Image.LANCZOS)
                logger.info(f"✅ Cached water icon from {path}")
                break
    except Exception as e:
        logger.error(f"Error caching water icon: {e}")

    # 5. Cyclone Icon
    try:
        icon_paths = [
            str(_STATIC_DIR / "cyclone_icon.png"),
            str(_STATIC_DIR / "storm_icon.png"),
        ]
        for path in icon_paths:
            if os.path.exists(path):
                raw_icon = Image.open(path).convert("RGBA")
                bbox = raw_icon.getbbox()
                if bbox:
                    raw_icon = raw_icon.crop(bbox)
                w, h = raw_icon.size
                target_h = 32
                target_w = max(4, int(target_h * (w / h)))
                state.GLOBAL_ICONS["cyclone"] = raw_icon.resize((target_w, target_h), Image.LANCZOS)
                logger.info(f"✅ Cached cyclone icon from {path}")
                break
    except Exception as e:
        logger.error(f"Error caching cyclone icon: {e}")


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
