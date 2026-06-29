import functools
import io
from pathlib import Path

import numpy as np
import rasterio
import mercantile
from loguru import logger
from rasterio.warp import reproject, Resampling
from PIL import Image

# api/app/services/tile_render/ → 5 levels up → MyCane_BE/
_BE_ROOT = Path(__file__).parent.parent.parent.parent.parent


@functools.lru_cache(maxsize=1024)
def _render_index_tile_cached(layer: str, layer_upper: str, z: int, x: int, y: int) -> bytes:
    indices_dir = _BE_ROOT / "data" / "processed" / "indices"
    latest_file = indices_dir / "latest" / f"latest_{layer_upper}.tif"

    if latest_file.exists():
        tif_file = latest_file
    else:
        if not indices_dir.exists():
            return b""
        scene_dirs = [d for d in indices_dir.iterdir() if d.is_dir() and d.name != "latest"]
        if not scene_dirs:
            return b""
        target_scene = max(scene_dirs, key=lambda d: d.stat().st_mtime)
        tif_file = target_scene / f"{target_scene.name}_{layer_upper}.tif"
        if not tif_file.exists():
            return b""

    try:
        with rasterio.open(tif_file) as src:
            bbox = mercantile.xy_bounds(x, y, z)

            dst_data = np.zeros((256, 256), dtype=np.float32)
            dst_transform = rasterio.transform.from_bounds(
                bbox.left, bbox.bottom, bbox.right, bbox.top, 256, 256
            )

            reproject(
                source=rasterio.band(src, 1),
                destination=dst_data,
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=dst_transform,
                dst_crs='EPSG:3857',
                resampling=Resampling.bilinear,
                init_dest_nodata=True
            )

            nodata_val = src.nodata if src.nodata is not None else -9999.0

            if layer == "ndvi":
                xp = [-1.0, -0.1, 0.2, 0.4, 0.8, 1.0]
                fp_r = [180, 215, 245, 190,  34,   0]
                fp_g = [ 30, 100, 220, 230, 139,  68]
                fp_b = [ 30,  40, 110,  90,  34,  27]
            elif layer == "ndmi":
                xp = [-1.0, -0.8, -0.2, 0.2, 0.8, 1.0]
                fp_r = [150, 210, 245, 150,  30,   0]
                fp_g = [ 75, 180, 235, 200, 100,  10]
                fp_b = [ 30, 140, 180, 250, 250, 150]
            else:  # nbr
                xp = [-1.0, -0.8, -0.2, 0.1, 0.8, 1.0]
                fp_r = [100, 220, 245, 215,  34,   0]
                fp_g = [  0,  20, 150, 220, 139,  68]
                fp_b = [ 80,  20,  50, 160,  34,  27]

            red   = np.interp(dst_data, xp, fp_r).astype(np.uint8)
            green = np.interp(dst_data, xp, fp_g).astype(np.uint8)
            blue  = np.interp(dst_data, xp, fp_b).astype(np.uint8)

            is_nodata = np.isnan(dst_data) | (dst_data == nodata_val) | (dst_data < -1.0) | (dst_data > 1.0)

            if np.all(is_nodata | (dst_data == 0.0)):
                return b""

            alpha = np.where(is_nodata, 0, 255).astype(np.uint8)

            rgba = np.stack([red, green, blue, alpha], axis=-1)

            img = Image.fromarray(rgba, "RGBA")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
    except Exception as e:
        logger.error(f"Error rendering raster tile: {e}")
        return b""
