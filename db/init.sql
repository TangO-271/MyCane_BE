-- ============================================
-- GEOAI Satellite Team - PostGIS Initial Schema
-- ============================================

-- Enable PostGIS extension
CREATE EXTENSION IF NOT EXISTS postgis;

-- ============================================
-- 0. Users (ผู้ใช้ในระบบ)
-- ============================================
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    role VARCHAR(50) DEFAULT 'farmer',
    subscription_tier VARCHAR(50) DEFAULT 'free',
    line_user_id VARCHAR(255),
    profile_image_url VARCHAR(512),
    phone VARCHAR(20),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================
-- 1. Plots (แปลงเกษตร)
-- ============================================
CREATE TABLE IF NOT EXISTS plots (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    plot_name VARCHAR(255),
    area_size NUMERIC(10,2),
    geometry GEOMETRY(Polygon, 32647),
    image_url VARCHAR(512),
    created_at TIMESTAMP DEFAULT NOW()
);

-- ============================================
-- 2. Hotspots (จุดความร้อน VIIRS)
-- ============================================
CREATE TABLE hotspots (
    id SERIAL PRIMARY KEY,
    latitude NUMERIC(10,6),
    longitude NUMERIC(10,6),
    brightness NUMERIC(8,2),
    confidence VARCHAR(10),
    satellite VARCHAR(20),
    acq_time TIMESTAMP,
    geometry GEOMETRY(Point, 32647),
    ingested_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_hotspots_geometry ON hotspots USING GIST(geometry);
CREATE INDEX idx_hotspots_acq_time ON hotspots(acq_time);

-- ============================================
-- 3. Plot Features (feature vector รายแปลง)
-- ============================================
CREATE TABLE plot_features (
    id SERIAL PRIMARY KEY,
    plot_id INTEGER REFERENCES plots(id),
    timestamp TIMESTAMP NOT NULL,
    data_freshness_days INTEGER,
    cloud_cover_pct NUMERIC(5,2),
    confidence VARCHAR(10),

    -- Indices
    ndvi NUMERIC(6,4),
    ndmi NUMERIC(6,4),
    nbr NUMERIC(6,4),

    -- Weather
    rain_7d_mm NUMERIC(8,2),
    humidity_pct NUMERIC(5,2),
    wind_speed_kmh NUMERIC(5,2),
    wind_direction_deg NUMERIC(5,2),      -- used by tile renderer for wind icons

    -- Fire
    hotspot_count_24h INTEGER DEFAULT 0,
    hotspot_count_7d INTEGER DEFAULT 0,
    nearest_hotspot_km NUMERIC(8,2),

    -- SPI
    spi_30d NUMERIC(6,3),

    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_plot_features_plot_id ON plot_features(plot_id);
CREATE INDEX idx_plot_features_timestamp ON plot_features(timestamp);

-- Unique constraint on (plot_id, timestamp) enables INSERT ... ON CONFLICT DO UPDATE
-- so pipeline runs are idempotent and history is preserved (no DELETE needed).
-- Migration for existing Supabase DB:
--   ALTER TABLE plot_features ADD CONSTRAINT uq_plot_features_plot_timestamp UNIQUE (plot_id, timestamp);
ALTER TABLE plot_features ADD CONSTRAINT uq_plot_features_plot_timestamp UNIQUE (plot_id, timestamp);

-- ============================================
-- 4. Burn Scars (ร่องรอยเผาไหม้)
-- ============================================
CREATE TABLE burn_scars (
    id SERIAL PRIMARY KEY,
    source VARCHAR(30),   -- 'gistda' or 'computed'
    detected_date DATE,
    area_sqm NUMERIC(12,2),
    geometry GEOMETRY(Polygon, 32647),
    ingested_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_burn_scars_geometry ON burn_scars USING GIST(geometry);
CREATE INDEX idx_burn_scars_date ON burn_scars(detected_date);

-- ============================================
-- 5. Plot Weather Timeseries (สภาพอากาศรายชั่วโมง/วัน แบบละเอียด)
-- ============================================
CREATE TABLE plot_weather_timeseries (
    id SERIAL PRIMARY KEY,
    plot_id INTEGER REFERENCES plots(id) ON DELETE CASCADE,
    forecast_time TIMESTAMP NOT NULL, -- เวลาที่พยากรณ์หรือตรวจวัด
    is_forecast BOOLEAN DEFAULT TRUE, -- true = พยากรณ์, false = ตรวจวัดจริง
    temperature_c NUMERIC(5,2),
    humidity_pct NUMERIC(5,2),
    rainfall_mm NUMERIC(8,2),
    wind_speed_kmh NUMERIC(5,2),
    wind_direction_deg NUMERIC(5,2),
    pressure_hpa NUMERIC(6,2),
    cloud_cover_pct NUMERIC(5,2),
    weather_condition VARCHAR(100), -- เช่น มีฝนฟ้าคะนอง, แดดจัด
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_weather_plot_time ON plot_weather_timeseries(plot_id, forecast_time);

-- ============================================
-- 6. Cyclone Tracks (เส้นทางเดินพายุหมุนเขตร้อน และสถิติคาบ 69 ปี)
-- ============================================
CREATE TABLE cyclone_tracks (
    id SERIAL PRIMARY KEY,
    cyclone_name VARCHAR(100),
    cyclone_year INTEGER,
    cyclone_month INTEGER,
    category VARCHAR(50), -- เช่น Depression, Tropical Storm, Typhoon
    max_wind_speed_kmh NUMERIC(6,2),
    geometry GEOMETRY(LineString, 32647), -- เส้นทางเดินพายุ
    data_source VARCHAR(50) DEFAULT 'TMD_69_YEARS_STATS',
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_cyclone_tracks_geom ON cyclone_tracks USING GIST(geometry);

-- ============================================
-- 7. Plot Cyclone Impacts (สถิติพายุที่กระทบแปลงเกษตร)
-- ============================================
CREATE TABLE plot_cyclone_impacts (
    id SERIAL PRIMARY KEY,
    plot_id INTEGER REFERENCES plots(id) ON DELETE CASCADE,
    cyclone_id INTEGER REFERENCES cyclone_tracks(id) ON DELETE CASCADE,
    distance_to_center_km NUMERIC(8,2),
    impact_level VARCHAR(20), -- High, Medium, Low
    recorded_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_plot_cyclones ON plot_cyclone_impacts(plot_id);

-- ============================================
-- 8. Notifications (ระบบแจ้งเตือนอัจฉริยะ — per-user alert inbox)
-- ============================================
CREATE TABLE IF NOT EXISTS notifications (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    plot_id INTEGER REFERENCES plots(id) ON DELETE SET NULL,
    title VARCHAR(255),
    message TEXT NOT NULL,
    hazard_type VARCHAR(20),                 -- fire | flood | drought | disease | system
    severity VARCHAR(20) DEFAULT 'info',     -- danger | warn | ok | info
    is_read BOOLEAN DEFAULT FALSE,
    channels VARCHAR(120),                   -- comma-joined channels actually dispatched (LINE, Firebase Push)
    dedupe_key VARCHAR(255),                 -- prevents the alert engine re-sending the same alert (per plot+hazard+day)
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, created_at DESC);
-- Full (non-partial) unique index so `ON CONFLICT (dedupe_key)` in the alert engine works.
-- Postgres treats NULLs as distinct, so /send notifications (NULL dedupe_key) still insert.
CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_dedupe ON notifications(dedupe_key);

-- ============================================
-- 10. Insert Demo Plot
-- ============================================
-- Insert default user for foreign key constraint consistency
INSERT INTO users (id, name, email, hashed_password, role)
VALUES (
    1,
    'Test Farmer',
    'farmer@heavenseye.com',
    '$2b$12$K8J/N4R3vO8Z1hN9qLp1eeN6bM6m5b7dK9a4bM6N1b1b1b1b1b1b1', -- bcrypt for 'Password123!'
    'farmer'
) ON CONFLICT (id) DO NOTHING;

INSERT INTO plots (id, user_id, plot_name, area_size, geometry)
VALUES (
    1,
    1,
    'DEMO-001',
    2.5,
    ST_Transform(
        ST_GeomFromText(
            'POLYGON((100.25 16.81, 100.27 16.81, 100.27 16.83, 100.25 16.83, 100.25 16.81))',
            4326
        ),
        32647
    )
) ON CONFLICT (id) DO NOTHING;

SELECT 'Database initialized successfully!' AS status;
