import math
from PIL import Image
from loguru import logger
from shapely.wkt import loads as wkt_loads
import app.core.state as state
import app.core.constants as const
from app.core.render_cache import _plots_in_tile
from app.services.tile_render.common import parse_tile_filters, draw_filled_polygon, risk_colors


def render_flood_tile(img, draw, to_pixels, cur,
                      west, south, east, north, buffer_meters, z,
                      user_id, plot_id):
    plot_id_int, user_id_int = parse_tile_filters(plot_id, user_id)

    # Use in-memory plot render cache — no DB query for plot/risk/feature data
    cyclone_icon = state.GLOBAL_ICONS.get("cyclone")
    wind_icon    = state.GLOBAL_ICONS.get("wind")
    global_wind_deg   = state._global_wind_cache["deg"]
    global_wind_speed = state._global_wind_cache["speed"]

    for entry in _plots_in_tile(west, south, east, north,
                                user_id_int=user_id_int, plot_id_int=plot_id_int):
        plot_geom_wkt = entry["geom_wkt"]
        risk_score    = entry["flood_risk"]
        row_wind_deg  = entry["wind_dir"]
        row_wind_speed = entry["wind_speed"]
        active_risk   = risk_score

        # 1. Draw the colored agricultural plot boundary polygon if it exists
        if plot_geom_wkt:
            try:
                geom = wkt_loads(plot_geom_wkt)
                # Color-code by flood risk — map colours = notification severity.
                fill_color, outline_color = risk_colors(active_risk)
                draw_filled_polygon(draw, to_pixels, geom, fill_color, outline_color)
            except Exception as e:
                logger.error(f"Error drawing flood colored plot boundary: {e}")

    # 2. Render Cyclone spiral vortex icon & wind vectors centered at the actual cyclone track points only at close zooms (z >= 11)
    if z >= 11:
        try:
            cyclone_query = """
                SELECT
                    id,
                    cyclone_name,
                    ST_AsText(ST_Transform(ST_Centroid(geometry), 4326)) as centroid,
                    max_wind_speed_kmh,
                    category
                FROM cyclone_tracks
                WHERE ST_Intersects(
                    geometry,
                    ST_Transform(
                        ST_Buffer(
                            ST_Transform(
                                ST_MakeEnvelope(%s, %s, %s, %s, 4326),
                                3857
                            ),
                            %s
                        ),
                        32647
                    )
                );
            """
            cur.execute(cyclone_query, (west, south, east, north, buffer_meters))
            cyclones = cur.fetchall()

            for cyc in cyclones:
                cyc_id, cyc_name, cyc_centroid_wkt, max_wind, category = cyc
                if not cyc_centroid_wkt:
                    continue

                pt = wkt_loads(cyc_centroid_wkt)
                lon, lat = pt.x, pt.y
                px, py = to_pixels(lon, lat)

                # Determine storm coloring: Typhoon level (red) or below (orange)
                is_typhoon = (max_wind is not None and float(max_wind) >= 118) or (category == "Typhoon")
                storm_color = (211, 47, 47, 240) if is_typhoon else (245, 124, 0, 240)

                # A. Render the custom cyclone PNG icon, or a beautiful high-contrast vector spiral fallback
                if cyclone_icon is not None:
                    iw, ih = cyclone_icon.size
                    img.paste(cyclone_icon, (int(px - iw / 2), int(py - ih / 2)), mask=cyclone_icon)
                else:
                    # Premium Vector fallback: Draw double-arm spiral vortex in storm red or orange
                    draw.ellipse([px-7, py-7, px+7, py+7], fill=storm_color, outline=(255, 255, 255, 255), width=1)
                    draw.arc([px-14, py-14, px+14, py+14], start=0, end=180, fill=storm_color, width=3)
                    draw.arc([px-14, py-14, px+14, py+14], start=180, end=360, fill=storm_color, width=3)

                # B. Render the rotated and scaled wind direction vector arrow next to it
                wind_speed = float(max_wind) if max_wind is not None else global_wind_speed
                wind_deg = global_wind_deg  # Fallback direction blowing NE

                arrow_dir = (wind_deg - 180) % 360
                rad = math.radians(arrow_dir)

                wx = px + 22
                wy = py + 14

                # White base anchor dot
                draw.ellipse([wx-3, wy-3, wx+3, wy+3], fill=(255, 255, 255, 255))

                if wind_icon is not None:
                    arr_size = int(max(const.WIND_ICON_MIN, min(const.WIND_ICON_MAX,
                                       const.WIND_ICON_BASE + wind_speed * const.WIND_ICON_SCALE)))
                    resized_w = wind_icon.resize((arr_size, arr_size), Image.LANCZOS)
                    rotated_w = resized_w.rotate((360 - arrow_dir) % 360, resample=Image.BICUBIC, expand=False)
                    img.paste(rotated_w, (int(wx - arr_size / 2), int(wy - arr_size / 2)), mask=rotated_w)
                else:
                    # Fallback arrow line
                    arr_len = max(26, min(44, 20 + wind_speed * 1.2))
                    ex = wx + arr_len * math.sin(rad)
                    ey = wy - arr_len * math.cos(rad)

                    draw.line([(wx+0.8, wy+0.8), (ex+0.8, ey+0.8)], fill=(0, 0, 0, 180), width=5)
                    draw.line([(wx, wy), (ex, ey)], fill=(0, 225, 255, 255), width=3)

                    hs = 8
                    hlx = ex - hs * math.sin(rad + math.radians(35))
                    hly = ey + hs * math.cos(rad + math.radians(35))
                    hrx = ex - hs * math.sin(rad - math.radians(35))
                    hry = ey + hs * math.cos(rad - math.radians(35))

                    draw.polygon([(ex+0.8, ey+0.8), (hlx+0.8, hly+0.8), (hrx+0.8, hry+0.8)], fill=(0, 0, 0, 180))
                    draw.polygon([(ex, ey), (hlx, hly), (hrx, hry)], fill=(0, 225, 255, 255))
        except Exception as ce:
            logger.error(f"Error rendering cyclone tracks: {ce}")
