from shapely.wkt import loads as wkt_loads
from app.services.tile_render.common import draw_filled_polygon


def render_burn_scar_tile(img, draw, to_pixels, cur, west, south, east, north):
    query = """
        SELECT ST_AsText(ST_Transform(geometry, 4326)), source, area_sqm
        FROM burn_scars
        WHERE ST_Intersects(
            geometry,
            ST_Transform(ST_MakeEnvelope(%s, %s, %s, %s, 4326), 32647)
        );
    """
    cur.execute(query, (west, south, east, north))
    rows = cur.fetchall()

    for row in rows:
        wkt, source, area = row
        geom = wkt_loads(wkt)
        draw_filled_polygon(draw, to_pixels, geom,
                            fill_color=(180, 50, 50, 100), outline_color=(180, 30, 30, 230), width=1)
