from pydantic import BaseModel
from typing import List, Dict, Any, Literal
from datetime import datetime

ConfidenceLevel = Literal["high", "medium", "low"]


class IndicesFeature(BaseModel):
    ndvi: float
    ndmi: float
    nbr: float


class WeatherFeature(BaseModel):
    rain_7d_mm: float
    humidity_pct: float
    wind_speed_kmh: float


class FireFeature(BaseModel):
    hotspot_count_24h: int
    hotspot_count_7d: int
    nearest_hotspot_km: float


class SPIFeature(BaseModel):
    spi_30d: float


class PlotFeatureResponse(BaseModel):
    plot_id: str
    timestamp: datetime
    data_freshness_days: int
    cloud_cover_pct: float
    confidence: ConfidenceLevel
    indices: IndicesFeature
    weather: WeatherFeature
    fire: FireFeature
    spi: SPIFeature


class PlotHistoryResponse(BaseModel):
    plot_id: str
    series: List[Dict[str, Any]]


class PlotCreateResponse(BaseModel):
    status: Literal["success"]
    message: str
    plot_id: str


class PlotCreateRequest(BaseModel):
    plot_id: str
    user_id: str
    geometry: Dict[str, Any]  # GeoJSON Geometry object
    crop_type: str
    province: str


class WeatherForecastDay(BaseModel):
    date: str                           # ISO date "YYYY-MM-DD"
    temp_c: float | None = None
    humidity_pct: float | None = None
    rainfall_mm: float | None = None
    wind_speed_kmh: float | None = None
    wind_direction_deg: float | None = None


class PlotForecastResponse(BaseModel):
    plot_id: str
    forecast: List[WeatherForecastDay]


class HotspotGeometry(BaseModel):
    type: str = "Point"
    coordinates: List[float]  # [lon, lat]


class HotspotProperties(BaseModel):
    brightness: float
    confidence: str
    acq_time: datetime
    satellite: str


class HotspotFeature(BaseModel):
    type: str = "Feature"
    geometry: HotspotGeometry
    properties: HotspotProperties


class HotspotCollection(BaseModel):
    type: str = "FeatureCollection"
    features: List[HotspotFeature]


class BurnScarProperties(BaseModel):
    source: str | None = None
    area_sqm: float | None = None


class BurnScarFeature(BaseModel):
    type: str = "Feature"
    geometry: Dict[str, Any]  # GeoJSON Polygon/MultiPolygon (WGS84)
    properties: BurnScarProperties


class BurnScarCollection(BaseModel):
    type: str = "FeatureCollection"
    features: List[BurnScarFeature]
