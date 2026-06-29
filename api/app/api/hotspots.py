from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.core.db_pool import get_raw_db
from app.models.geo import (
    HotspotCollection, HotspotFeature, HotspotGeometry, HotspotProperties,
)
from app.utils.format import safe_float, normalize_confidence

router = APIRouter()


@router.get("/hotspots", response_model=HotspotCollection)
def get_hotspots(
    bbox: str = Query(..., description="min_lon,min_lat,max_lon,max_lat"),
    hours: int = Query(default=24, ge=1, le=168),
    response: Response = None,
    conn=Depends(get_raw_db)
):
    """ดึงจุดความร้อนในพื้นที่ที่กำหนด"""
    try:
        coords = [float(c) for c in bbox.split(",")]
        if len(coords) != 4:
            raise ValueError()
    except ValueError:
        raise HTTPException(status_code=400, detail="Bbox must be in format min_lon,min_lat,max_lon,max_lat")

    min_lon, min_lat, max_lon, max_lat = coords
    cutoff_time = datetime.now() - timedelta(hours=hours)

    cur = conn.cursor()
    query = """
        SELECT latitude, longitude, brightness, confidence, acq_time, satellite
        FROM hotspots
        WHERE acq_time >= %s
          AND longitude >= %s AND latitude >= %s
          AND longitude <= %s AND latitude <= %s
        ORDER BY acq_time DESC;
    """
    cur.execute(query, (cutoff_time, min_lon, min_lat, max_lon, max_lat))
    rows = cur.fetchall()
    cur.close()

    features = []
    for r in rows:
        lat, lon, brightness, confidence, acq_time, satellite = r
        features.append(HotspotFeature(
            geometry=HotspotGeometry(coordinates=[float(lon), float(lat)]),
            properties=HotspotProperties(
                brightness=safe_float(brightness),
                confidence=normalize_confidence(confidence),
                acq_time=acq_time,
                satellite=str(satellite) if satellite else "VIIRS_SNPP"
            )
        ))

    # Hotspots update hourly from VIIRS; 5-min fresh cache, 10-min stale window.
    if response is not None:
        response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=600"
    return HotspotCollection(features=features)
