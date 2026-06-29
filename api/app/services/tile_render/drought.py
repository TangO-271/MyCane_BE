from loguru import logger
from shapely.wkt import loads as wkt_loads
import app.core.state as state
from app.core.render_cache import _plots_in_tile
from app.services.tile_render.common import parse_tile_filters, draw_filled_polygon


def render_drought_tile(img, draw, to_pixels,
                        west, south, east, north,
                        user_id, plot_id):
    plot_id_int, user_id_int = parse_tile_filters(plot_id, user_id)

    # Use in-memory plot render cache — no DB query for plot/risk/feature data
    water_icon = state.GLOBAL_ICONS.get("water")

    for entry in _plots_in_tile(west, south, east, north,
                                user_id_int=user_id_int, plot_id_int=plot_id_int):
        plot_geom_wkt = entry["geom_wkt"]
        centroid_wkt  = entry["centroid_wkt"]
        row_ndmi      = entry["ndmi"]
        row_humidity  = entry["humidity_pct"]

        # 1. Draw the colored agricultural plot boundary polygon if it exists
        if plot_geom_wkt:
            try:
                geom = wkt_loads(plot_geom_wkt)

                # Determine NDMI 3-level color based on the user's specs:
                # 0.4 to 1.0: Healthy, lush vegetation or dense forest with no water stress (Green)
                # 0.2 to 0.4: Sparse or stressed crops; moderate canopy cover with mild water stress (Orange)
                # < 0.2: Bare soil, urban structures, or sparse canopy with high water stress (Red)
                ndmi_val = row_ndmi  # already a float from cache

                if ndmi_val >= 0.4:
                    fill_color = (74, 138, 42, 205)
                    outline_color = (56, 105, 32, 220)
                elif ndmi_val >= 0.2:
                    fill_color = (245, 124, 0, 205)
                    outline_color = (230, 81, 0, 220)
                else:
                    fill_color = (211, 47, 47, 205)
                    outline_color = (183, 28, 28, 220)

                draw_filled_polygon(draw, to_pixels, geom, fill_color, outline_color)
            except Exception as e:
                logger.error(f"Error drawing drought colored plot boundary: {e}")

        # 2. Render warning indicator if the plot is in medium or worst level (NDMI < 0.4)
        ndmi_val     = row_ndmi       # already a float from cache
        humidity_val = row_humidity

        if ndmi_val < 0.4:
            if not centroid_wkt:
                continue

            try:
                pt = wkt_loads(centroid_wkt)
                lon, lat = pt.x, pt.y
                px, py = to_pixels(lon, lat)

                if water_icon is not None:
                    iw, ih = water_icon.size
                    img.paste(water_icon, (int(px - iw / 2), int(py - ih / 2)), mask=water_icon)
                else:
                    # Fallback circle with exclamation / droplet
                    if humidity_val > 60.0:
                        draw.ellipse([px-10, py-10, px+10, py+10], fill=(33, 150, 243, 240), outline=(255, 255, 255, 255), width=2)
                        draw.polygon([(px, py-14), (px-5, py-5), (px+5, py-5)], fill=(33, 150, 243, 240))
                    else:
                        draw.ellipse([px-10, py-10, px+10, py+10], fill=(211, 47, 47, 240), outline=(255, 193, 7, 255), width=2)
                        draw.line([(px, py-5), (px, py+1)], fill=(255, 255, 255, 255), width=2)
                        draw.ellipse([px-1, py+3, px+1, py+5], fill=(255, 255, 255, 255))
            except Exception as e:
                logger.error(f"Error rendering individual drought plot marker: {e}")
