import hashlib
import io
import math
from datetime import datetime
from typing import Optional

import mercantile
from fastapi import APIRouter, HTTPException, Query, Request, Response
from loguru import logger
from PIL import Image, ImageDraw

import app.core.state as state
import app.core.constants as const
from app.utils.format import parse_plot_id
from app.services.tile_render.raster import _render_index_tile_cached
from app.services.tile_render.hotspot import render_hotspot_tile
from app.services.tile_render.disease import render_disease_tile
from app.services.tile_render.drought import render_drought_tile
from app.services.tile_render.flood import render_flood_tile
from app.services.tile_render.burn_scar import render_burn_scar_tile

router = APIRouter()

_VECTOR_LAYERS = {"hotspot", "burn_scar", "disease", "drought", "flood"}
_RASTER_LAYERS = {"ndvi", "ndmi", "nbr"}
_DB_LAYERS     = {"hotspot", "burn_scar", "flood"}


@router.get("/tiles/{layer}/{z}/{x}/{y}.png")
def get_tile(
    layer: str,
    z: int,
    x: int,
    y: int,
    request: Request = None,
    user_id: Optional[str] = Query(None),
    plot_id: Optional[str] = Query(None),
):
    layer = layer.lower()
    if layer not in (_RASTER_LAYERS | _VECTOR_LAYERS):
        raise HTTPException(status_code=400, detail=f"Unsupported layer: {layer}")

    if layer in {"disease", "drought"}:
        if not user_id and not plot_id:
            raise HTTPException(
                status_code=401,
                detail=f"Authentication required: user_id or plot_id query parameter is required for the '{layer}' layer."
            )
        try:
            if plot_id:
                parse_plot_id(plot_id)
            elif user_id:
                # user_id is a plain integer, not a PLT-xxx plot id.
                int(user_id)
        except Exception:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid identifier format provided for the '{layer}' layer."
            )

    current_hour = datetime.utcnow().strftime("%Y-%m-%d-%H")
    etag_raw = f"{layer}_{z}_{x}_{y}_{user_id or ''}_{plot_id or ''}_{current_hour}"
    etag_val = hashlib.md5(etag_raw.encode()).hexdigest()
    etag = f'"{etag_val}"'

    if request:
        if_none_match = request.headers.get("if-none-match")
        if if_none_match == etag:
            return Response(
                status_code=304,
                headers={
                    "Cache-Control": const.CACHE_TILE_SHORT,
                    "ETag": etag,
                },
            )

    def empty_tile():
        if state.EMPTY_TILE_BYTES is None:
            img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            state.EMPTY_TILE_BYTES = buf.getvalue()
        return Response(
            content=state.EMPTY_TILE_BYTES,
            media_type="image/png",
            headers={
                "Cache-Control": const.CACHE_TILE_LONG,
                "ETag": etag,
            },
        )

    if layer in _RASTER_LAYERS:
        png_bytes = _render_index_tile_cached(layer, layer.upper(), z, x, y)
        if not png_bytes:
            return empty_tile()
        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={
                "Cache-Control": const.CACHE_TILE_SHORT,
                "ETag": etag,
            },
        )

    # ── vector layers ──────────────────────────────────────────────────────────
    with state._vtile_lock:
        if state._vtile_cache_hour != current_hour:
            state._vector_tile_cache.clear()
            state._vtile_cache_hour = current_hour
        cached_png = state._vector_tile_cache.get(etag_val)
    if cached_png is not None:
        return Response(
            content=cached_png,
            media_type="image/png",
            headers={
                "Cache-Control": const.CACHE_TILE_SHORT,
                "ETag": etag,
            },
        )

    _needs_db = layer in _DB_LAYERS
    conn = state.db_pool.getconn() if _needs_db else None
    cur = None
    try:
        bounds = mercantile.bounds(x, y, z)
        west, south, east, north = bounds.west, bounds.south, bounds.east, bounds.north

        xy_bounds = mercantile.xy_bounds(x, y, z)
        min_x, min_y, max_x, max_y = xy_bounds.left, xy_bounds.bottom, xy_bounds.right, xy_bounds.top
        buffer_meters = (max_x - min_x) * (64.0 / 256.0)

        def to_pixels(lng, lat):
            mx = lng * const.WEB_MERCATOR_HALF / 180.0
            my = math.log(math.tan((90.0 + lat) * math.pi / 360.0)) / (math.pi / 180.0)
            my = my * const.WEB_MERCATOR_HALF / 180.0
            px = 256.0 * (mx - min_x) / (max_x - min_x)
            py = 256.0 * (max_y - my) / (max_y - min_y)
            return px, py

        img  = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        cur  = conn.cursor() if conn is not None else None

        if layer == "hotspot":
            render_hotspot_tile(img, draw, to_pixels, cur,
                                west, south, east, north, buffer_meters,
                                user_id, plot_id)
        elif layer == "disease":
            render_disease_tile(img, draw, to_pixels,
                                west, south, east, north,
                                user_id, plot_id)
        elif layer == "drought":
            render_drought_tile(img, draw, to_pixels,
                                west, south, east, north,
                                user_id, plot_id)
        elif layer == "flood":
            render_flood_tile(img, draw, to_pixels, cur,
                              west, south, east, north, buffer_meters, z,
                              user_id, plot_id)
        elif layer == "burn_scar":
            render_burn_scar_tile(img, draw, to_pixels, cur,
                                  west, south, east, north)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        with state._vtile_lock:
            state._vector_tile_cache[etag_val] = png_bytes

        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={
                "Cache-Control": const.CACHE_TILE_SHORT,
                "ETag": etag,
            },
        )

    except Exception as e:
        logger.error(f"Error rendering vector tile: {e}")
        return empty_tile()
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
            state.db_pool.putconn(conn)
