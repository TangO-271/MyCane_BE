# 📡 Data Source Catalog — Satellite Team

> รายละเอียดแหล่งข้อมูลภูมิสารสนเทศทั้งหมด

---

## สรุปแหล่งข้อมูล

| # | แหล่งข้อมูล | ความถี่ | Resolution | ใช้สำหรับ | Format |
|---|-------------|---------|------------|----------|--------|
| 1 | Sentinel-2 L2A | 5 วัน | 10-20m | NDVI, NDMI, NBR, Burn Scar | COG |
| 2 | VIIRS (NASA FIRMS) | Near real-time | 375m | จุดความร้อน (Hotspot) | CSV/GeoJSON |
| 3 | CHIRPS v2.0 | รายวัน | ~5.5km | ปริมาณฝน, SPI | NetCDF |
| 4 | GISTDA Burn Scar | รายวัน | - | ร่องรอยเผาไหม้ | GeoJSON |
| 5 | Copernicus GLO-30 DEM | คงที่ | 30m | Slope, Aspect, River Distance | GeoTIFF |
| 6 | TMD + Open-Meteo | รายวัน | - | อุณหภูมิ, ความชื้น, ลม, ฝนพยากรณ์ | JSON |
| 7 | LDD Soil | คงที่ | - | ดิน, การใช้ที่ดิน | PostGIS (DB) |
| 8 | OSM + DOAE | คงที่ | - | ถนน, แหล่งน้ำ, ขอบเขตแปลง | PBF/GeoJSON |

---

## 1. Sentinel-2 L2A (Copernicus)

- **API**: `https://catalogue.dataspace.copernicus.eu/stac` (STAC API)
- **Bands**: B2(Blue), B3(Green), B4(Red), B8(NIR), B11(SWIR1), B12(SWIR2), SCL
- **Processing Level**: L2A (Bottom-of-Atmosphere reflectance)
- **พื้นที่**: Tile T47QNA, T47QNB
- **⚠️ ข้อจำกัด**: เมฆบังบ่อยในฤดูฝน → ใช้ historical fallback + แสดง Confidence ต่ำ

```python
from pystac_client import Client
catalog = Client.open("https://catalogue.dataspace.copernicus.eu/stac")
search = catalog.search(
    collections=["sentinel-2-l2a"],
    bbox=[99.5, 15.5, 101.5, 17.5],
    datetime="2026-01-01/2026-05-17",
    query={"eo:cloud_cover": {"lt": 30}}
)
```

## 2. VIIRS Active Fire (NASA FIRMS)

- **API**: `https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/VIIRS_SNPP_NRT/{bbox}/{days}`
- **⚠️ ข้อจำกัด**: Geolocation error ±375m → Buffer Zone 30m รอบแปลง

## 3. CHIRPS v2.0

- **URL**: `https://data.chc.ucsb.edu/products/CHIRPS-2.0/`
- **SPI** = (P - μ) / σ → SPI < -1.0 = แล้งรุนแรง, SPI > +1.0 = ฝนมากผิดปกติ

## 4. GISTDA Burn Scar — ผ่าน Sphere API

## 5. Copernicus GLO-30 DEM — Static, ดาวน์โหลดครั้งเดียว

## 6. TMD + Open-Meteo

```
GET https://api.open-meteo.com/v1/forecast
    ?latitude=16.82&longitude=100.26
    &daily=temperature_2m_max,precipitation_sum,relative_humidity_2m_mean
    &forecast_days=14&timezone=Asia/Bangkok
```

## 7-8. LDD Soil + OSM — Static datasets, load เข้า PostGIS ครั้งเดียว

---

## Data Flow สรุป

```
Source           → Storage          → Consumer
─────────────────────────────────────────────────
Sentinel-2       → S3/MinIO (COG)   → Index Computation → AI Team
VIIRS            → PostGIS          → AI Team (Fire Risk)
CHIRPS           → Open Data Cube   → SPI → AI Team
Burn Scar        → PostGIS          → AI Team (Fire Risk)
DEM              → S3/MinIO         → Slope/River Dist → AI Team
TMD/Open-Meteo   → PostgreSQL       → AI Team (Disease Risk)
LDD/OSM          → PostGIS          → AI Team (Flood/Fire)
```
