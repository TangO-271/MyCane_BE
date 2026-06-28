"""Data Quality Analysis - ตรวจสอบข้อมูลใน plot_features"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from pipeline.config import DATABASE_URL
import psycopg2

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

print("=" * 80)
print("📊 DATA QUALITY ANALYSIS — plot_features")
print("=" * 80)

cur.execute("SELECT COUNT(*) FROM plot_features")
print(f"\n📌 Total records: {cur.fetchone()[0]}")

cur.execute("SELECT confidence, COUNT(*) FROM plot_features GROUP BY confidence ORDER BY COUNT(*) DESC")
print(f"\n🎯 Confidence Distribution:")
for r in cur.fetchall(): print(f"   {r[0]}: {r[1]}")

cur.execute("SELECT cloud_cover_pct, COUNT(*) FROM plot_features GROUP BY cloud_cover_pct ORDER BY COUNT(*) DESC LIMIT 5")
print(f"\n☁️  Cloud Cover (top 5):")
for r in cur.fetchall(): print(f"   {r[0]}%: {r[1]} records")

cur.execute("SELECT data_freshness_days, COUNT(*) FROM plot_features GROUP BY data_freshness_days ORDER BY COUNT(*) DESC LIMIT 5")
print(f"\n📅 Data Freshness (top 5):")
for r in cur.fetchall(): print(f"   {r[0]} days: {r[1]} records")

cur.execute("SELECT COUNT(*) FILTER (WHERE ndvi=0), COUNT(*) FILTER (WHERE ndvi>0), ROUND(AVG(ndvi)::numeric, 4) FILTER (WHERE ndvi>0), COUNT(*) FROM plot_features")
r = cur.fetchone()
print(f"\n🌱 NDVI:  zero={r[0]}, non-zero={r[1]}, avg(non-zero)={r[2]}, total={r[3]}")

cur.execute("SELECT COUNT(*) FILTER (WHERE ndmi=0), COUNT(*) FILTER (WHERE ndmi>0), ROUND(AVG(ndmi)::numeric, 4) FILTER (WHERE ndmi>0), COUNT(*) FROM plot_features")
r = cur.fetchone()
print(f"💧 NDMI:  zero={r[0]}, non-zero={r[1]}, avg(non-zero)={r[2]}, total={r[3]}")

cur.execute("SELECT COUNT(*) FILTER (WHERE nbr=0), COUNT(*) FILTER (WHERE nbr!=0), ROUND(AVG(CASE WHEN nbr!=0 THEN nbr END)::numeric, 4), COUNT(*) FROM plot_features")
r = cur.fetchone()
print(f"🔥 NBR:   zero={r[0]}, non-zero={r[1]}, avg(non-zero)={r[2]}, total={r[3]}")

cur.execute("SELECT COUNT(*) FILTER (WHERE river_distance_m=1200), COUNT(*) FILTER (WHERE river_distance_m!=1200), COUNT(*) FROM plot_features")
r = cur.fetchone()
print(f"\n💧 River Distance: fallback(1200m)={r[0]}, real={r[1]}, total={r[2]}")

cur.execute("SELECT COUNT(*) FILTER (WHERE wind_speed_kmh=0 OR wind_speed_kmh IS NULL), COUNT(*) FILTER (WHERE wind_speed_kmh>0), COUNT(*) FROM plot_features")
r = cur.fetchone()
print(f"💨 Wind Speed: zero/null={r[0]}, real={r[1]}, total={r[2]}")

cur.execute("SELECT COUNT(*) FILTER (WHERE burn_scar_recurrence=0), COUNT(*) FILTER (WHERE burn_scar_recurrence>0), COUNT(*) FROM plot_features")
r = cur.fetchone()
print(f"🔥 Burn Scar Recurrence: zero={r[0]}, non-zero={r[1]}, total={r[2]}")

cur.execute("SELECT COUNT(*) FILTER (WHERE hotspot_historical_density=0 OR hotspot_historical_density IS NULL), COUNT(*) FILTER (WHERE hotspot_historical_density>0), COUNT(*) FROM plot_features")
r = cur.fetchone()
print(f"🔥 Hotspot Hist Density: zero={r[0]}, non-zero={r[1]}, total={r[2]}")

cur.execute("SELECT COUNT(*) FILTER (WHERE spi_30d=0 OR spi_30d IS NULL), COUNT(*) FILTER (WHERE spi_30d!=0), COUNT(*) FROM plot_features")
r = cur.fetchone()
print(f"\n📊 SPI 30d: zero/null={r[0]}, real={r[1]}, total={r[2]}")

cur.execute("SELECT ROUND(MIN(temp_min_c)::numeric,1), ROUND(MAX(temp_max_c)::numeric,1), COUNT(*) FILTER (WHERE temp_max_c=0 OR temp_max_c IS NULL), COUNT(*) FROM plot_features")
r = cur.fetchone()
print(f"🌡️  Temp: min={r[0]}, max={r[1]}, zero/null={r[2]}, total={r[3]}")

cur.execute("SELECT COUNT(DISTINCT soil_water_capacity), MIN(soil_water_capacity), MAX(soil_water_capacity) FROM plot_features")
r = cur.fetchone()
print(f"🌱 Soil Water Cap: {r[0]} distinct, range: {r[1]} - {r[2]}")

cur.execute("""SELECT p.plot_name, pf.ndvi, pf.ndmi, pf.nbr
FROM plot_features pf JOIN plots p ON pf.plot_id=p.id
WHERE pf.ndvi > 0.01 ORDER BY pf.ndvi DESC LIMIT 10""")
print(f"\n✅ Plots with real NDVI (inside tile):")
for r in cur.fetchall():
    print(f"   {r[0]}: NDVI={float(r[1]):.4f}, NDMI={float(r[2]):.4f}, NBR={float(r[3]):.4f}")

cur.execute("SELECT COUNT(*) FROM burn_scars")
print(f"\n🔥 Burn Scars table: {cur.fetchone()[0]} records")

# Province breakdown
cur.execute("""
SELECT 
    CASE 
        WHEN p.plot_name LIKE 'SPOT-PHI%' THEN 'Phitsanulok'
        WHEN p.plot_name LIKE 'SPOT-PHE%' THEN 'Phetchabun'
        WHEN p.plot_name LIKE 'SPOT-NAK%' THEN 'Nakhon Sawan'
        WHEN p.plot_name LIKE 'SPOT-SUP%' THEN 'Suphan Buri'
        WHEN p.plot_name LIKE 'SPOT-UTT%' THEN 'Uttaradit'
        ELSE 'Other'
    END AS province,
    COUNT(*),
    ROUND(AVG(pf.ndvi)::numeric, 4),
    ROUND(AVG(pf.ndmi)::numeric, 4),
    ROUND(AVG(pf.nbr)::numeric, 4)
FROM plot_features pf
JOIN plots p ON pf.plot_id = p.id
GROUP BY province
ORDER BY province
""")
print(f"\n📍 By Province:")
print(f"   {'Province':<18} {'Count':>5} {'Avg NDVI':>10} {'Avg NDMI':>10} {'Avg NBR':>10}")
for r in cur.fetchall():
    print(f"   {r[0]:<18} {r[1]:>5} {r[2]:>10} {r[3]:>10} {r[4]:>10}")

conn.close()
