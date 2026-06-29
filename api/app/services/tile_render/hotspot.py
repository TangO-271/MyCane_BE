import math
from PIL import Image
from loguru import logger
from shapely.wkt import loads as wkt_loads
import app.core.state as state
import app.core.constants as const
from app.core.render_cache import _plots_in_tile
from app.services.tile_render.common import parse_tile_filters, draw_filled_polygon, risk_colors


def render_hotspot_tile(img, draw, to_pixels, cur,
                        west, south, east, north, buffer_meters,
                        user_id, plot_id):
    # 1. Draw plots colored by fire_risk_score — from in-memory cache (no DB query)
    plot_id_int, user_id_int = parse_tile_filters(plot_id, user_id)

    cached_plots = _plots_in_tile(west, south, east, north,
                                  user_id_int=user_id_int, plot_id_int=plot_id_int)

    for entry in cached_plots:
        fire_risk = entry["fire_risk"]
        plot_geom_wkt = entry["geom_wkt"]
        active_risk = fire_risk

        if plot_geom_wkt:
            try:
                geom = wkt_loads(plot_geom_wkt)
                # Color-code by fire risk — map colours = notification severity.
                fill_color, outline_color = risk_colors(active_risk)
                draw_filled_polygon(draw, to_pixels, geom, fill_color, outline_color)
            except Exception as e:
                logger.error(f"Error drawing fire colored plot boundary: {e}")

    # 2. Draw hotspots and wind vectors
    # Spatial lateral join to retrieve wind speed and direction from the nearest plot_features record for each hotspot,
    # with highly dynamic, locally accurate meteorological rendering.
    query = """
        SELECT
            h.longitude,
            h.latitude,
            h.brightness,
            h.confidence,
            w.wind_direction_deg,
            w.wind_speed_kmh
        FROM hotspots h
        LEFT JOIN LATERAL (
            SELECT pf.wind_direction_deg, pf.wind_speed_kmh
            FROM plots p
            JOIN plot_features pf ON p.id = pf.plot_id
            WHERE pf.wind_direction_deg IS NOT NULL AND pf.wind_speed_kmh IS NOT NULL
            ORDER BY p.geometry <-> h.geometry, pf.timestamp DESC
            LIMIT 1
        ) w ON TRUE
        WHERE ST_Intersects(
            h.geometry,
            ST_Buffer(
                ST_Transform(
                    ST_MakeEnvelope(%s, %s, %s, %s, 4326),
                    32647
                ),
                %s
            )
        );
    """
    cur.execute(query, (west, south, east, north, buffer_meters))
    rows = cur.fetchall()

    # Use globally preloaded fire icon and wind icon
    fire_icon = state.GLOBAL_ICONS.get("fire")
    wind_icon = state.GLOBAL_ICONS.get("wind")

    # Use cached global wind as fallback (refreshed hourly after sync)
    global_wind_deg   = state._global_wind_cache["deg"]
    global_wind_speed = state._global_wind_cache["speed"]

    for row in rows:
        lon, lat, brightness, confidence, row_wind_deg, row_wind_speed = row
        px, py = to_pixels(float(lon), float(lat))

        # Resolve spatial wind direction (fallback to global latest if NULL)
        wind_deg   = float(row_wind_deg)   if row_wind_deg   is not None else global_wind_deg
        wind_speed = float(row_wind_speed) if row_wind_speed is not None else global_wind_speed

        # 1. Draw a highly visible wind direction vector arrow (meteorological wind blows FROM, arrow points TO)
        arrow_dir = (wind_deg - 180) % 360
        rad = math.radians(arrow_dir)

        # Center of wind arrow is offset bottom-right (wx = px + 22, wy = py + 14) so it aligns perfectly with the larger flame
        wx = px + 22
        wy = py + 14

        # Larger high-contrast base anchor dot
        draw.ellipse([wx-3, wy-3, wx+3, wy+3], fill=(255, 255, 255, 255))

        if wind_icon is not None:
            # Dynamic wind icon size based on wind speed (26–42px), rounded to nearest
            # even step so it hits the pre-computed variant table (no per-hotspot resize).
            arr_size_raw = max(const.WIND_ICON_MIN, min(const.WIND_ICON_MAX,
                                int(const.WIND_ICON_BASE + wind_speed * const.WIND_ICON_SCALE)))
            arr_size = round(arr_size_raw / 2) * 2   # snap to 26, 28, ..., 42
            angle_slot = round(arrow_dir / 22.5) % 16

            rotated_w = state.WIND_ICON_VARIANTS.get((arr_size, angle_slot))
            if rotated_w is None:
                # Fallback for edge cases (variants not yet built)
                resized_w = wind_icon.resize((arr_size, arr_size), Image.LANCZOS)
                rotated_w = resized_w.rotate((360 - arrow_dir) % 360, resample=Image.BICUBIC, expand=False)

            # Paste the rotated wind icon centered at (wx, wy)
            img.paste(rotated_w, (int(wx - arr_size / 2), int(wy - arr_size / 2)), mask=rotated_w)
        else:
            # Fallback: Draw wind line with a strong dark drop shadow for maximum 3D popup and high-contrast map visibility
            arr_len = max(26, min(44, 20 + wind_speed * 1.2))
            ex = wx + arr_len * math.sin(rad)
            ey = wy - arr_len * math.cos(rad)

            draw.line([(wx+0.8, wy+0.8), (ex+0.8, ey+0.8)], fill=(0, 0, 0, 180), width=5)
            draw.line([(wx, wy), (ex, ey)], fill=(0, 225, 255, 255), width=3)

            # Massive sharp arrowhead pointing in the direction of the wind
            hs = 8
            hlx = ex - hs * math.sin(rad + math.radians(35))
            hly = ey + hs * math.cos(rad + math.radians(35))
            hrx = ex - hs * math.sin(rad - math.radians(35))
            hry = ey + hs * math.cos(rad - math.radians(35))

            # Draw thick arrowhead drop shadow & arrow body
            draw.polygon([(ex+0.8, ey+0.8), (hlx+0.8, hly+0.8), (hrx+0.8, hry+0.8)], fill=(0, 0, 0, 180))
            draw.polygon([(ex, ey), (hlx, hly), (hrx, hry)], fill=(0, 225, 255, 255))

        # 2. Render the custom PNG fire icon
        if fire_icon is not None:
            # Paste the actual custom fire icon image centered at the coordinates (no extra circular halos/css icons)
            iw, ih = fire_icon.size
            img.paste(fire_icon, (int(px - iw / 2), int(py - ih / 2)), mask=fire_icon)
        else:
            # Fallback: Outer flame body (Precise asymmetrical double-peak shape matching the 3rd image)
            outer_coords = [
                (px - 1, py - 14),
                (px - 5, py - 9),
                (px - 9, py - 3),
                (px - 11, py + 3),
                (px - 10, py + 9),
                (px - 5, py + 13),
                (px, py + 14),
                (px + 5, py + 13),
                (px + 10, py + 9),
                (px + 11, py + 4),
                (px + 9, py - 1),
                (px + 7, py - 4),
                (px + 8, py - 7),
                (px + 5, py - 3),
                (px + 3, py - 8)
            ]
            draw.polygon(outer_coords, fill=(255, 107, 24, 255), outline=None)
