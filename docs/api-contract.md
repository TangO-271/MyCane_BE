# 🔌 API Contract — Satellite Team ↔ AI Team / App Team

> ข้อตกลง API ระหว่าง Satellite Team กับทีมอื่น

---

## Overview

Satellite Team ให้บริการ 2 ประเภท:

1. **Feature API** → ส่งข้อมูล geo-feature รายแปลงให้ **AI Team**
2. **Tile API** → ส่ง map tiles ให้ **App Team** แสดงผลบนแผนที่

---

## 1. Feature API (สำหรับ AI Team)

### `GET /api/v1/features/{plot_id}`

ดึง feature ล่าสุดของแปลง

**Response:**
```json
{
  "plot_id": "PLT-001",
  "timestamp": "2026-05-15T06:00:00Z",
  "data_freshness_days": 2,
  "cloud_cover_pct": 12.5,
  "confidence": "high",
  "indices": {
    "ndvi": 0.72,
    "ndmi": 0.35,
    "nbr": 0.48
  },
  "weather": {
    "rain_7d_mm": 45.2,
    "rain_forecast_14d_mm": 120.0,
    "temp_max_c": 35.2,
    "temp_min_c": 24.1,
    "humidity_pct": 78.5,
    "wind_speed_kmh": 12.3,
    "wind_direction_deg": 225
  },
  "terrain": {
    "elevation_m": 45.0,
    "slope_deg": 3.2,
    "aspect_deg": 180.0,
    "river_distance_m": 1200.0
  },
  "fire": {
    "hotspot_count_24h": 0,
    "hotspot_count_7d": 2,
    "nearest_hotspot_km": 4.5,
    "burn_scar_recurrence": 0.15,
    "hotspot_historical_density": 0.08
  },
  "soil": {
    "land_use_type": "rice_paddy",
    "soil_water_capacity": 0.65
  },
  "spi": {
    "spi_30d": -0.45,
    "spi_60d": -0.82,
    "spi_90d": -0.33
  }
}
```

### `GET /api/v1/features/{plot_id}/history`

ดึง time series ของ feature (สำหรับ trend analysis)

**Query Params:**
- `start_date` (required): ISO date
- `end_date` (required): ISO date
- `indices` (optional): comma-separated list of indices

**Response:**
```json
{
  "plot_id": "PLT-001",
  "series": [
    {
      "timestamp": "2026-05-10T06:00:00Z",
      "ndvi": 0.68,
      "ndmi": 0.31,
      "nbr": 0.45
    },
    {
      "timestamp": "2026-05-15T06:00:00Z",
      "ndvi": 0.72,
      "ndmi": 0.35,
      "nbr": 0.48
    }
  ]
}
```

### `GET /api/v1/hotspots`

ดึง hotspot ในพื้นที่

**Query Params:**
- `bbox` (required): `min_lon,min_lat,max_lon,max_lat`
- `hours` (optional, default=24): ย้อนหลังกี่ชั่วโมง

**Response:**
```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "geometry": { "type": "Point", "coordinates": [100.26, 16.82] },
      "properties": {
        "brightness": 320.5,
        "confidence": "high",
        "acq_time": "2026-05-17T03:15:00Z",
        "satellite": "VIIRS_SNPP"
      }
    }
  ]
}
```

---

## 2. Tile API (สำหรับ App Team)

### `GET /tiles/{layer}/{z}/{x}/{y}.png`

**Layers ที่ให้บริการ:**

| Layer | คำอธิบาย | Color Ramp |
|-------|---------|------------|
| `ndvi` | สุขภาพพืช | Red → Yellow → Green |
| `ndmi` | ความชื้นพืช | Brown → Blue |
| `nbr` | ร่องรอยเผา | Red → Orange → Green |
| `rainfall` | ปริมาณฝน | White → Blue → Purple |
| `burn_scar` | พื้นที่เผาไหม้ | Transparent → Red |
| `hotspot` | จุดความร้อน | Point markers (red) |

**Query Params:**
- `date` (optional): ISO date, default = latest
- `colormap` (optional): custom colormap name

---

## 3. Plot Management (รับจาก App Team)

### `POST /api/v1/plots`

App Team ส่ง polygon ของแปลงมาให้ Satellite Team เก็บ

**Request:**
```json
{
  "plot_id": "PLT-001",
  "user_id": "USR-001",
  "geometry": {
    "type": "Polygon",
    "coordinates": [[[100.25, 16.81], [100.26, 16.81], [100.26, 16.82], [100.25, 16.82], [100.25, 16.81]]]
  },
  "crop_type": "rice",
  "province": "phitsanulok"
}
```

---

## Data Schema (Shared)

### Plot Feature Vector → AI Team

```python
# Pydantic Model
class PlotFeature(BaseModel):
    plot_id: str
    timestamp: datetime
    data_freshness_days: int
    cloud_cover_pct: float
    confidence: Literal["high", "medium", "low"]
    
    # Indices
    ndvi: float       # -1 to 1
    ndmi: float       # -1 to 1
    nbr: float        # -1 to 1
    
    # Weather
    rain_7d_mm: float
    rain_forecast_14d_mm: float
    temp_max_c: float
    humidity_pct: float
    
    # Terrain (static)
    elevation_m: float
    slope_deg: float
    river_distance_m: float
    
    # Fire
    hotspot_count_7d: int
    burn_scar_recurrence: float
    
    # Soil
    land_use_type: str
    soil_water_capacity: float
    
    # SPI
    spi_30d: float
    spi_60d: float
    spi_90d: float
```

### Confidence Score Calculation

```
confidence = f(data_freshness, cloud_cover, sensor_agreement)

Rules:
  - data_freshness ≤ 3 days AND cloud_cover < 20%  → "high"
  - data_freshness ≤ 7 days AND cloud_cover < 50%  → "medium"
  - else                                            → "low"
```

---

## Error Codes

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `PLOT_NOT_FOUND` | 404 | ไม่พบแปลงที่ระบุ |
| `NO_DATA_AVAILABLE` | 404 | ไม่มีข้อมูลในช่วงเวลาที่ขอ |
| `STALE_DATA` | 200 | มีข้อมูลแต่เก่ากว่า 7 วัน (ส่งกลับพร้อม confidence: low) |
| `PROCESSING` | 202 | กำลังประมวลผลอยู่ ลองใหม่อีกครั้ง |
| `INTERNAL_ERROR` | 500 | ข้อผิดพลาดภายใน |
