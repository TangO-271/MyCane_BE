# 🏗️ Pipeline Architecture — Satellite Team

> สถาปัตยกรรม Data Pipeline สำหรับประมวลผลข้อมูลดาวเทียม

---

## Architecture Diagram

```
                        ┌──────────────┐
                        │ API & Cron   │
                        │ (FastAPI)    │
                        └──────┬───────┘
                               │ hourly batch trigger
                               ▼
    ┌──────────────────────────────────────────────────┐
    │          Batch Orchestrator (run_poc.py)         │
    │  ┌─────────┐  ┌───────────┐  ┌───────────────┐  │
    │  │ Query   │→ │ Loop All  │→ │ Save Features │  │
    │  │ Plots   │  │ Plots     │  │ to DB & S3    │  │
    │  └─────────┘  └───────────┘  └───────────────┘  │
    └──────────────────────┬───────────────────────────┘
                           │ For each plot:
                           ▼
    ┌──────────────────────────────────────────────────┐
    │              Data Ingestion Steps                │
    │  ┌──────────┐ ┌───────────┐ ┌────────────────┐  │
    │  │ Weather  │ │ Soil (LDD)│ │ Fire (FIRMS)   │  │
    │  │ (O-Meteo)│ │ (PostGIS) │ │ (API)          │  │
    │  └──────────┘ └───────────┘ └────────────────┘  │
    │  ┌──────────┐ ┌───────────┐                     │
    │  │ Sentinel │ │ NDVI/NDMI │                     │
    │  │ Download │ │ Calculate │                     │
    │  └──────────┘ └───────────┘                     │
    └──────────────────────┬───────────────────────────┘
                           │
    ┌──────────────────────────────────────────────────┐
    │              OUTPUT                              │
    │  PostGIS │ JSON Feature API │ S3/MinIO           │
    └──────────────────────────────────────────────────┘
```

---

## Processing Flow

### 1. Hourly Batch Pipeline (`run_poc.py`)
- ระบบถูกตั้งเวลา (APScheduler) ให้รันทุกๆ 1 ชั่วโมง
- **ขั้นตอนการทำงาน:**
  1. `Query Plots`: ดึงข้อมูลพิกัด (Lat/Lon) ของเกษตรกรทุกแปลงจากฐานข้อมูล
  2. `Dynamic Override`: วนลูปและป้อนพิกัดให้ Data Ingestion Scripts
  3. `Ingestion`: ดึงสภาพอากาศ (Open-Meteo), ข้อมูลดินแท้ (LDD PostGIS), สแกนจุดความร้อน, โหลดภาพถ่ายดาวเทียม
  4. `Zonal Stats`: คำนวณค่าดัชนี (NDVI) ให้ตรงกับรูปแปลง
  5. `Database Insert`: อัปเดตข้อมูลล่าสุดลงตาราง `plot_features` เพื่อให้ API พร้อมเสิร์ฟ AI Team



## Storage Architecture

```
MinIO/S3
├── raw/
│   ├── sentinel2/
│   │   └── {date}/{tile}/B{band}.tif
│   ├── chirps/
│   │   └── {date}/chirps_daily.nc
│   └── dem/
│       └── glo30_thailand.tif
├── processed/
│   ├── indices/
│   │   └── {date}/{tile}/
│   │       ├── ndvi.tif (COG)
│   │       ├── ndmi.tif (COG)
│   │       └── nbr.tif (COG)
│   └── zonal_stats/
│       └── {date}/plot_features.parquet
└── tiles/
    └── {layer}/{z}/{x}/{y}.png
```

---

## Pre-Processing Details

### Cloud Mask (SCL Band)
```python
# Sentinel-2 Scene Classification Layer
VALID_CLASSES = [4, 5, 6, 7]  # Vegetation, Bare Soil, Water, Unclassified
CLOUD_CLASSES = [3, 8, 9, 10] # Cloud Shadow, Medium/High Cloud, Cirrus

mask = np.isin(scl_band, VALID_CLASSES)
masked_band = np.where(mask, band_data, np.nan)
```

### Coordinate System
- **ทั้งระบบใช้ EPSG:32647** (UTM Zone 47N)
- ทุก dataset ต้อง reproject ก่อนเข้า processing

### Resample
- Target: 10m grid (match Sentinel-2 native resolution)
- Method: Bilinear interpolation สำหรับ continuous data

---

## Index Computation Formulas

| Index | สูตร | ช่วงค่า | ความหมาย |
|-------|------|---------|----------|
| NDVI | (B8-B4)/(B8+B4) | -1 to +1 | สุขภาพพืช (>0.6 = สมบูรณ์) |
| NDMI | (B8-B11)/(B8+B11) | -1 to +1 | ความชื้นพืช (>0.4 = ชื้นดี) |
| NBR | (B8-B12)/(B8+B12) | -1 to +1 | พื้นที่เผาไหม้ (<0.1 = เผา) |
| SPI | (P-μ)/σ | unbounded | ฝนผิดปกติ (<-1 = แล้ง) |

---

## Environment Variables

```env
# S3/MinIO
S3_ENDPOINT=http://minio:9000
S3_ACCESS_KEY=<access_key>
S3_SECRET_KEY=<secret_key>
S3_BUCKET=satellite-data

# Database
POSTGIS_HOST=localhost
POSTGIS_PORT=5432
POSTGIS_DB=geoai
POSTGIS_USER=satellite
POSTGIS_PASSWORD=<password>

# APIs
COPERNICUS_CLIENT_ID=<client_id>
COPERNICUS_CLIENT_SECRET=<client_secret>
FIRMS_MAP_KEY=<map_key>
SPHERE_API_KEY=<sphere_key>
TMD_API_KEY=<tmd_key>

# Airflow
AIRFLOW__CORE__DAGS_FOLDER=/opt/airflow/dags
```
