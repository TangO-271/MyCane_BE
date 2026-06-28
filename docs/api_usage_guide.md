# 📡 คู่มือการเชื่อมต่อ API (Satellite Team)

คู่มือนี้จัดทำขึ้นสำหรับ **AI Team** และ **App Team** เพื่อใช้ดึงข้อมูลภาพถ่ายดาวเทียม, สภาพอากาศ, และจุดความร้อนแบบ Real-time จาก Satellite Team

> [!IMPORTANT]  
> **Base URL (Production):**  
> `https://heaveneye-geoai-satellite.hf.space`

---

## 🤖 สำหรับ AI Team (Feature Data)
AI Team จะเน้นการดึงข้อมูลตัวเลขและสถิติ (Feature Vector) เพื่อนำไปเข้าโมเดล Machine Learning

### 1. ดึงข้อมูลล่าสุดของแปลง (Latest Feature)
ใช้สำหรับดึงข้อมูลสภาพแวดล้อมล่าสุดเพื่อทำ Prediction
- **Endpoint:** `GET /api/v1/features/{plot_id}`
- **ตัวอย่าง:** `https://heaveneye-geoai-satellite.hf.space/api/v1/features/PLT-001`

**ตัวอย่าง Response:**
```json
{
  "plot_id": "PLT-001",
  "timestamp": "2026-05-15T06:00:00Z",
  "confidence": "high",
  "indices": { "ndvi": 0.72, "ndmi": 0.35, "nbr": 0.48 },
  "weather": {
    "rain_7d_mm": 45.2,
    "temp_max_c": 35.2,
    "humidity_pct": 78.5
  },
  "fire": {
    "hotspot_count_7d": 2,
    "nearest_hotspot_km": 4.5
  }
}
```

### 2. ดึงประวัติข้อมูล (Time-Series History)
ใช้สำหรับดูแนวโน้ม (Trend) ย้อนหลังของแต่ละ Index
- **Endpoint:** `GET /api/v1/features/{plot_id}/history`
- **Query Params:** 
  - `start_date` (required) เช่น `2026-05-01T00:00:00`
  - `end_date` (required)
  - `indices` (optional) เช่น `ndvi,nbr`

---

## 📱 สำหรับ App Team (Map & UI)
App Team จะเน้นการดึงรูปภาพ (Map Tiles) ไปแสดงซ้อนบนแผนที่ (Google Maps / Leaflet) และจัดการแปลงเกษตร

### 1. ดึงชั้นข้อมูลแผนที่ (Map Tiles)
ใช้สำหรับดึงรูปภาพแบบ XYZ Tile ไปแปะบนแผนที่ (Web/Mobile)
- **Endpoint:** `GET /api/v1/tiles/{layer}/{z}/{x}/{y}.png`
- **ตัวอย่างการใช้ใน Leaflet/Mapbox:**
  `https://heaveneye-geoai-satellite.hf.space/api/v1/tiles/ndvi/{z}/{x}/{y}.png`

> [!TIP]
> **Layer ที่รองรับ:**
> - `ndvi` (สุขภาพพืช: แดง → เขียว)
> - `ndmi` (ความชื้นพืช: น้ำตาล → น้ำเงิน)
> - `nbr` (ร่องรอยเผาไหม้: แดง → เขียว)
> - `hotspot` (จุดความร้อนปัจจุบัน: วงกลมสีแดง)

### 2. เพิ่มแปลงเกษตรใหม่ (Create Plot)
เมื่อ User วาดแปลงใหม่บนแอป ให้ยิงข้อมูลมาบันทึกที่ Satellite Team ด้วย
- **Endpoint:** `POST /api/v1/plots`
- **Body (JSON):**
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

### 3. ดึงจุดความร้อนล่าสุด (Hotspots)
ใช้สำหรับแสดง Pin บนแผนที่ว่ามีไฟป่าใกล้ๆ แปลงหรือไม่
- **Endpoint:** `GET /api/v1/hotspots`
- **Query Params:** 
  - `bbox` (required): พิกัดขอบเขตแผนที่หน้าจอผู้ใช้ (min_lon,min_lat,max_lon,max_lat)
  - `hours` (optional): ย้อนหลังกี่ชั่วโมง (ค่าเริ่มต้น: 24)

---

## ⚠️ รหัสข้อผิดพลาด (Error Codes)

| HTTP Status | ความหมาย | สิ่งที่ App/AI ควรทำ |
|-------------|---------|--------------------|
| **200** | สำเร็จ | ใช้งานข้อมูลได้ตามปกติ |
| **202** | `PROCESSING` | ระบบดาวเทียมกำลังคำนวณ ให้ตั้งหน่วงเวลาแล้วลองเรียกใหม่ |
| **404** | `PLOT_NOT_FOUND` | ไม่พบแปลงนี้ในระบบ ให้เช็ค ID |
| **500** | `INTERNAL_ERROR` | เซิร์ฟเวอร์ดาวเทียมมีปัญหา แจ้ง Satellite Team |
