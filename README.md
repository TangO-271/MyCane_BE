---
title: GEOAI Satellite
emoji: 🛰️
colorFrom: blue
colorTo: green
sdk: docker
pinned: false
---
# 🛰️ Satellite Team — ทีม Geo-Data

> โปรเจกต์ "ตาสวรรค์" | Geospatial Intelligence for Resilience Hackathon 2026

---

## 📌 ภาพรวมทีม

**Satellite Team** รับผิดชอบ **Data Pipeline ฝั่ง Geo-Spatial** ทั้งหมด
ตั้งแต่ดึงข้อมูลดาวเทียม → ประมวลผล → คำนวณ Index → ส่ง Feature ให้ AI Team ใช้

```
┌─────────────────────────────────────────────────────────┐
│                    DATA SOURCES                         │
│  Sentinel-2 │ VIIRS │ CHIRPS │ DEM │ Sphere API │ TMD  │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│              INGESTION PIPELINE (Airflow)                │
│  Schedule fetch → Download → Store raw to S3/MinIO       │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│              PRE-PROCESSING                              │
│  Cloud Mask → Reproject (EPSG:32647) → Resample (10m)    │
│  → Parcel Zonal Stats                                    │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│              INDEX COMPUTATION                           │
│  NDVI │ NDMI │ NBR │ SPI │ Slope │ Aspect │ RiverDist   │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│              OUTPUT LAYER                                │
│  PostGIS (vector) │ Open Data Cube (raster) │ TiTiler    │
│  Feature API → AI Team │ Tile Server → App Team          │
└─────────────────────────────────────────────────────────┘
```

---

## 👥 สมาชิกและบทบาท

| ชื่อ | บทบาท | ความรับผิดชอบหลัก |
|------|--------|-------------------|
| **พิริยกร** | Satellite Lead | Pipeline Architecture, Geo-feature API, ประสานงาน AI/App Team |

> **Bridge Role**: ดูแล Geo-feature API ที่เป็นจุดเชื่อมกับ AI Team

---

## 🚀 Quick Start — เริ่มต้นใช้งาน

### Prerequisites
- **Python 3.10+** — [Download](https://www.python.org/downloads/)
- **Docker Desktop** (optional) — [Download](https://www.docker.com/products/docker-desktop/)
- **Git** — [Download](https://git-scm.com/)

### วิธีติดตั้ง (คำสั่งเดียว)

**Windows (PowerShell):**
```powershell
git clone <repo-url>
cd satellite-team
.\setup.ps1                # ติดตั้งปกติ
.\setup.ps1 -WithDocker    # ติดตั้ง + เปิด PostGIS/MinIO
```

**Mac / Linux:**
```bash
git clone <repo-url>
cd satellite-team
chmod +x setup.sh
./setup.sh                 # ติดตั้งปกติ
./setup.sh --docker        # ติดตั้ง + เปิด PostGIS/MinIO
```

### หลังติดตั้งเสร็จ
```bash
# 1. เปิด venv
.\venv\Scripts\Activate.ps1       # Windows
source venv/bin/activate           # Mac/Linux

# 2. ใส่ API Keys ใน .env
notepad .env                       # Windows
nano .env                          # Mac/Linux

# 3. ทดสอบ
python run_poc.py
```

> ⚠️ อย่าลืมใส่ API Keys ใน `.env` ก่อนรัน — ดูวิธีขอในส่วน [API Keys](#-api-keys-ที่ต้องขอ)

---

## 🗂️ โครงสร้างโฟลเดอร์

```
satellite-team/
├── README.md                      ← ไฟล์นี้
├── docs/
│   ├── data-source-catalog.md     ← รายละเอียดแหล่งข้อมูลทั้งหมด
│   ├── pipeline-architecture.md   ← สถาปัตยกรรม Pipeline
│   ├── api-contract.md            ← API Contract กับ AI/App Team
│   └── validation-plan.md         ← แผนตรวจสอบข้อมูล
├── pipeline/                      ← โค้ด Data Pipeline
│   ├── dags/                      ← Airflow DAGs
│   ├── ingestion/                 ← Scripts ดึงข้อมูลจากแหล่งต่างๆ
│   ├── preprocessing/             ← Cloud mask, reproject, resample
│   ├── index_computation/         ← NDVI, NDMI, NBR, SPI
│   └── zonal_stats/               ← Parcel-level statistics
├── api/                           ← Geo-feature REST API
│   ├── routes/
│   ├── services/
│   └── schemas/
├── tile-server/                   ← TiTiler / GeoServer config
├── data/                          ← ตัวอย่างข้อมูล (gitignore large files)
│   ├── raw/
│   ├── processed/
│   └── validation/
├── tests/
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

---

## ⏰ Timeline (5 สัปดาห์)

| สัปดาห์ | งานหลัก | Deliverable | ต้องส่งต่อให้ |
|---------|---------|-------------|--------------|
| **W1** | Setup Sphere Map API + Open Data Cube + ทดลองดึง Sentinel-2 | Environment ready, sample data | - |
| **W2** | Pipeline ingest + reproject + cloud mask | Automated ingestion DAG | AI Team (raw features) |
| **W3** | Compute NDVI/NDMI/NBR/SPI + Zonal Stats | Feature API v1 | AI Team (features per plot) |
| **W4** | Tile Server + ส่ง feature ให้ AI | Map tiles + Feature API v2 | App Team (tile endpoint) |
| **W5** | Validation + Tornado Diagram (Sensitivity ±20%) | Validation Report | ทุกทีม |

---

## 🔑 API Keys ที่ต้องขอ

| Service | URL | สถานะ |
|---------|-----|--------|
| GISTDA Sphere API | https://sphere.gistda.or.th | ✅ ได้รับและกำหนดค่าแล้ว |
| Copernicus Data Space | https://dataspace.copernicus.eu | ✅ ได้รับและกำหนดค่าแล้ว |
| NASA FIRMS (VIIRS) | https://firms.modaps.eosdis.nasa.gov | ✅ ได้รับและกำหนดค่าแล้ว |
| TMD Open API | https://data.tmd.go.th | ✅ ได้รับและกำหนดค่าแล้ว |
| Open-Meteo | https://open-meteo.com | ✅ ไม่ต้องขอ (Free API) |

---

## 🛠️ Tech Stack

| หมวด | เทคโนโลยี |
|------|-----------|
| **ภาษาหลัก** | Python 3.11+ |
| **Geo Libraries** | rasterio, xarray, geopandas, shapely, pyproj |
| **Pipeline Orchestration** | Apache Airflow / Prefect |
| **Raster DB** | Open Data Cube |
| **Vector DB** | PostgreSQL + PostGIS |
| **Tile Server** | TiTiler (Cloud-Optimized GeoTIFF) |
| **Storage** | AWS S3 / MinIO (COG format) |
| **STAC API** | pystac-client |
| **Processing** | GDAL, scipy |
| **Containerization** | Docker + Docker Compose |

---

## 📎 เอกสารที่เกี่ยวข้อง

- [Data Source Catalog](docs/data-source-catalog.md)
- [Pipeline Architecture](docs/pipeline-architecture.md)
- [API Contract](docs/api-contract.md)
- [Validation Plan](docs/validation-plan.md)
