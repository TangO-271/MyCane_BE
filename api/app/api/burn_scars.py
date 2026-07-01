import json

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.core.db_pool import get_raw_db
import app.core.constants as const
from app.models.geo import (
    BurnScarCollection, BurnScarFeature, BurnScarProperties,
)
from app.utils.format import safe_float

router = APIRouter()


@router.get("/burn_scars", response_model=BurnScarCollection)
def get_burn_scars(
    bbox: str = Query(..., description="min_lon,min_lat,max_lon,max_lat"),
    response: Response = None,
    conn=Depends(get_raw_db),
):
    """ดึงรอยไฟไหม้ (burn scars) ในพื้นที่ที่กำหนด เป็น GeoJSON polygons (WGS84)"""
    try:
        coords = [float(c) for c in bbox.split(",")]
        if len(coords) != 4:
            raise ValueError()
    except ValueError:
        raise HTTPException(status_code=400, detail="Bbox must be in format min_lon,min_lat,max_lon,max_lat")

    west, south, east, north = coords

    cur = conn.cursor()
    query = """
        SELECT ST_AsGeoJSON(ST_Transform(geometry, 4326)), source, area_sqm
        FROM burn_scars
        WHERE ST_Intersects(
            geometry,
            ST_Transform(ST_MakeEnvelope(%s, %s, %s, %s, 4326), 32647)
        );
    """
    cur.execute(query, (west, south, east, north))
    rows = cur.fetchall()
    cur.close()

    features = []
    for geojson_str, source, area in rows:
        if not geojson_str:
            continue
        features.append(BurnScarFeature(
            geometry=json.loads(geojson_str),
            properties=BurnScarProperties(
                source=str(source) if source else None,
                area_sqm=safe_float(area),
            ),
        ))

    # Burn scars change at most daily (pipeline is daily) — long-lived cache.
    if response is not None:
        response.headers["Cache-Control"] = const.CACHE_TILE_LONG
    return BurnScarCollection(features=features)
