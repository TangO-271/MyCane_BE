import math
from loguru import logger
from shapely.wkt import loads as wkt_loads
import app.core.state as state
from app.core.render_cache import _plots_in_tile
from app.services.tile_render.common import parse_tile_filters, draw_filled_polygon


def render_disease_tile(img, draw, to_pixels,
                        west, south, east, north,
                        user_id, plot_id):
    plot_id_int, user_id_int = parse_tile_filters(plot_id, user_id)

    # Use in-memory plot render cache — no DB query for plot/risk/feature data
    disease_icon = state.GLOBAL_ICONS.get("disease")

    for entry in _plots_in_tile(west, south, east, north,
                                user_id_int=user_id_int, plot_id_int=plot_id_int):
        plot_geom_wkt = entry["geom_wkt"]
        centroid_wkt  = entry["centroid_wkt"]
        row_ndvi      = entry["ndvi"]

        # 1. First draw the colored agricultural plot boundary polygon if it exists
        if plot_geom_wkt:
            try:
                geom = wkt_loads(plot_geom_wkt)

                # Determine NDVI 4-level color based on the user's uploaded spec (non-green tones):
                # -1.0 to 0.0: Dead plant or object (Gray)
                # 0.0 to 0.33: Unhealthy plant (Crimson Red)
                # 0.33 to 0.66: Moderately healthy plant (Pumpkin Orange)
                # 0.66 to 1.0: Very healthy plant (Golden Amber/Yellow)
                plot_ndvi = row_ndvi  # already a float from cache

                if plot_ndvi <= 0.0:
                    fill_color = (158, 158, 158, 205)
                    outline_color = (117, 117, 117, 220)
                elif plot_ndvi <= 0.33:
                    fill_color = (211, 47, 47, 205)
                    outline_color = (183, 28, 28, 220)
                elif plot_ndvi <= 0.66:
                    fill_color = (245, 124, 0, 205)
                    outline_color = (230, 81, 0, 220)
                else:
                    fill_color = (74, 138, 42, 205)
                    outline_color = (56, 105, 32, 220)

                draw_filled_polygon(draw, to_pixels, geom, fill_color, outline_color)
            except Exception as e:
                logger.error(f"Error drawing colored plot boundary: {e}")

        # 2. Render crop disease icon if the plot is in medium or worst level (NDVI <= 0.66)
        plot_ndvi = row_ndvi  # already a float from cache

        if plot_ndvi <= 0.66:
            if not centroid_wkt:
                continue

            try:
                pt = wkt_loads(centroid_wkt)
                lon, lat = pt.x, pt.y
                px, py = to_pixels(lon, lat)

                # Draw custom disease PNG icon, or a beautiful non-green virus fallback polygon
                if disease_icon is not None:
                    iw, ih = disease_icon.size
                    img.paste(disease_icon, (int(px - iw / 2), int(py - ih / 2)), mask=disease_icon)
                else:
                    # Premium non-green fallback
                    draw.ellipse([px-12, py-12, px+12, py+12], fill=(211, 47, 47, 240), outline=(255, 193, 7, 255), width=2)
                    for ang in range(0, 360, 45):
                        r_ang = math.radians(ang)
                        sx = px + 15 * math.sin(r_ang)
                        sy = py - 15 * math.cos(r_ang)
                        draw.ellipse([sx-3, sy-3, sx+3, sy+3], fill=(255, 193, 7, 255))
            except Exception as e:
                logger.error(f"Error rendering individual disease plot marker: {e}")
