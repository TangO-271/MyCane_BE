import json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.core.db_pool import get_raw_db
from app.models.geo import (
    PlotFeatureResponse, PlotHistoryResponse,
    PlotCreateResponse, PlotCreateRequest,
)
from app.utils.format import (
    parse_plot_id, format_plot_id,
    safe_float, build_plot_feature_response,
    PLOT_FEATURE_SELECT_COLUMNS, SUPPORTED_HISTORY_INDICES,
)

router = APIRouter()


@router.get("/features", response_model=List[PlotFeatureResponse])
def get_all_latest_features(conn=Depends(get_raw_db)):
    """ดึง feature ล่าสุดของทุกแปลงพร้อมกัน สำหรับส่งให้ AI Team (Batch Processing)"""
    cur = conn.cursor()
    query = f"""
        SELECT DISTINCT ON (plot_id)
               {PLOT_FEATURE_SELECT_COLUMNS}
        FROM plot_features
        ORDER BY plot_id, timestamp DESC;
    """
    cur.execute(query)
    rows = cur.fetchall()
    cur.close()
    return [build_plot_feature_response(row) for row in rows]


@router.get("/features/{plot_id}", response_model=PlotFeatureResponse)
def get_latest_feature(plot_id: str, response: Response, conn=Depends(get_raw_db)):
    """ดึง feature ล่าสุดของแปลงเกษตรสำหรับส่งให้ AI Team"""
    plot_id_int = parse_plot_id(plot_id)
    cur = conn.cursor()
    query = f"""
        SELECT {PLOT_FEATURE_SELECT_COLUMNS}
        FROM plot_features
        WHERE plot_id = %s
        ORDER BY timestamp DESC
        LIMIT 1;
    """
    cur.execute(query, (plot_id_int,))
    r = cur.fetchone()
    cur.close()

    if not r:
        raise HTTPException(status_code=404, detail="PLOT_NOT_FOUND or NO_DATA_AVAILABLE")

    # Features update only during daily ingestion; 5-min fresh cache, 1-hour stale window.
    response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=3600"
    return build_plot_feature_response(r)


@router.get("/features/{plot_id}/history", response_model=PlotHistoryResponse)
def get_feature_history(
    plot_id: str,
    start_date: datetime = Query(..., description="ISO date start boundary"),
    end_date: datetime = Query(..., description="ISO date end boundary"),
    indices: Optional[str] = Query(default=None, description="comma-separated list of indices"),
    conn=Depends(get_raw_db)
):
    """ดึง time series ของ feature สำหรับ trend analysis"""
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="end_date must be greater than or equal to start_date")

    selected_indices = ["ndvi", "ndmi", "nbr"]
    if indices:
        selected_indices = [item.strip().lower() for item in indices.split(",") if item.strip()]
        invalid_indices = sorted(set(selected_indices) - SUPPORTED_HISTORY_INDICES)
        if invalid_indices:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported indices requested: {', '.join(invalid_indices)}"
            )

    plot_id_int = parse_plot_id(plot_id)
    cur = conn.cursor()
    query = """
        SELECT timestamp, ndvi, ndmi, nbr
        FROM plot_features
        WHERE plot_id = %s
          AND timestamp >= %s
          AND timestamp <= %s
        ORDER BY timestamp ASC;
    """
    cur.execute(query, (plot_id_int, start_date, end_date))
    rows = cur.fetchall()
    cur.close()

    if not rows:
        raise HTTPException(status_code=404, detail="NO_DATA_AVAILABLE")

    series = []
    for r in rows:
        point = {"timestamp": r[0]}
        if "ndvi" in selected_indices:
            point["ndvi"] = safe_float(r[1])
        if "ndmi" in selected_indices:
            point["ndmi"] = safe_float(r[2])
        if "nbr" in selected_indices:
            point["nbr"] = safe_float(r[3])
        series.append(point)

    return PlotHistoryResponse(plot_id=format_plot_id(plot_id_int), series=series)


@router.post("/plots", status_code=201, response_model=PlotCreateResponse)
def create_plot(request: PlotCreateRequest, conn=Depends(get_raw_db)):
    """App Team ส่ง polygon ของแปลงมาให้ Satellite Team เก็บ"""
    plot_id_int = parse_plot_id(request.plot_id)
    user_id_int = parse_plot_id(request.user_id)
    geom_geojson = json.dumps(request.geometry)

    cur = conn.cursor()
    try:
        query = """
            WITH input_geom AS (
                SELECT ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), 32647) AS geometry
            )
            INSERT INTO plots (id, user_id, plot_name, area_size, geometry)
            SELECT
                %s,
                %s,
                %s,
                ST_Area(geometry) / 10000.0,
                geometry
            FROM input_geom
            ON CONFLICT (id) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                plot_name = EXCLUDED.plot_name,
                area_size = EXCLUDED.area_size,
                geometry = EXCLUDED.geometry
            RETURNING id;
        """
        cur.execute(query, (geom_geojson, plot_id_int, user_id_int, request.plot_id))
        inserted_id = cur.fetchone()[0]
        conn.commit()
        return PlotCreateResponse(
            status="success",
            message=f"Plot {request.plot_id} successfully created/updated.",
            plot_id=format_plot_id(inserted_id)
        )
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    finally:
        cur.close()
