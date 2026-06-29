from typing import Any, Literal, Sequence
from fastapi import HTTPException
from app.models.geo import (
    PlotFeatureResponse,
    IndicesFeature,
    WeatherFeature,
    FireFeature,
    SPIFeature,
)

# Ordered column list for SELECT from plot_features.
# !! Keep row indices in sync with build_plot_feature_response() below !!
PLOT_FEATURE_SELECT_COLUMNS = """
    plot_id,            -- row[0]
    timestamp,          -- row[1]
    data_freshness_days,-- row[2]
    cloud_cover_pct,    -- row[3]
    confidence,         -- row[4]
    ndvi,               -- row[5]
    ndmi,               -- row[6]
    nbr,                -- row[7]
    rain_7d_mm,         -- row[8]
    humidity_pct,       -- row[9]
    wind_speed_kmh,     -- row[10]
    hotspot_count_24h,  -- row[11]
    hotspot_count_7d,   -- row[12]
    nearest_hotspot_km, -- row[13]
    spi_30d             -- row[14]
"""

SUPPORTED_HISTORY_INDICES = {"ndvi", "ndmi", "nbr"}


def parse_plot_id(plot_id_str: str) -> int:
    """แปลง plot_id เช่น 'PLT-001' หรือ '1' ให้เป็น integer ID"""
    if isinstance(plot_id_str, int):
        return plot_id_str
    digits = "".join(filter(str.isdigit, plot_id_str))
    if digits:
        return int(digits)
    # ถ้าไม่มีตัวเลข ให้ลองแปลงโดยตรง หรือยกเว้น error
    try:
        return int(plot_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid plot_id format: {plot_id_str}. Must contain digits.")


def format_plot_id(plot_id_int: int) -> str:
    """แปลง integer ID กลับเป็น string เช่น 'PLT-001'"""
    return f"PLT-{plot_id_int:03d}"


def safe_float(value: Any, default: float = 0.0) -> float:
    """แปลงค่าเป็น float แบบปลอดภัย"""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    """แปลงค่าเป็น int แบบปลอดภัย"""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_confidence(value: Any) -> Literal["high", "medium", "low"]:
    """Normalize confidence values from DB/source systems to contract values."""
    raw_value = str(value).strip().lower() if value is not None else ""
    mapping = {
        "high": "high",
        "h": "high",
        "medium": "medium",
        "m": "medium",
        "nominal": "medium",
        "n": "medium",
        "low": "low",
        "l": "low",
    }
    return mapping.get(raw_value, "low")


def build_plot_feature_response(row: Sequence[Any]) -> PlotFeatureResponse:
    """Map a plot_features row into the nested API contract."""
    return PlotFeatureResponse(
        plot_id=format_plot_id(safe_int(row[0])),
        timestamp=row[1],
        data_freshness_days=safe_int(row[2]),
        cloud_cover_pct=safe_float(row[3]),
        confidence=normalize_confidence(row[4]),
        indices=IndicesFeature(
            ndvi=safe_float(row[5]),
            ndmi=safe_float(row[6]),
            nbr=safe_float(row[7]),
        ),
        weather=WeatherFeature(
            rain_7d_mm=safe_float(row[8]),
            humidity_pct=safe_float(row[9]),
            wind_speed_kmh=safe_float(row[10]),
        ),
        fire=FireFeature(
            hotspot_count_24h=safe_int(row[11]),
            hotspot_count_7d=safe_int(row[12]),
            nearest_hotspot_km=safe_float(row[13], default=999.0),
        ),
        spi=SPIFeature(
            spi_30d=safe_float(row[14]),
        ),
    )
