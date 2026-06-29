from shapely.wkt import loads as wkt_loads


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

        def draw_geom(g):
            if g.geom_type == 'Polygon':
                ext_coords = [to_pixels(lon, lat) for lon, lat in g.exterior.coords]
                if len(ext_coords) >= 3:
                    draw.polygon(ext_coords, fill=(180, 50, 50, 100), outline=(180, 30, 30, 230))
                for interior in g.interiors:
                    int_coords = [to_pixels(lon, lat) for lon, lat in interior.coords]
                    if len(int_coords) >= 3:
                        draw.polygon(int_coords, fill=(0, 0, 0, 0), outline=(180, 30, 30, 230))
            elif g.geom_type == 'MultiPolygon':
                for poly in g.geoms:
                    draw_geom(poly)

        draw_geom(geom)
