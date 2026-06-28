import os
import sys
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
import urllib.parse

load_dotenv()

POSTGIS_HOST = os.getenv("POSTGIS_HOST")
POSTGIS_PORT = os.getenv("POSTGIS_PORT", "5432")
POSTGIS_DB = os.getenv("POSTGIS_DB", "postgres")
POSTGIS_USER = os.getenv("POSTGIS_USER", "postgres")
POSTGIS_PASSWORD = os.getenv("POSTGIS_PASSWORD")

encoded_password = urllib.parse.quote_plus(POSTGIS_PASSWORD)
db_url = f"postgresql://{POSTGIS_USER}:{encoded_password}@{POSTGIS_HOST}:{POSTGIS_PORT}/{POSTGIS_DB}"

engine = create_engine(db_url)

print("🔄 Starting Update of Old Plots...")

with engine.connect() as conn:
    # Get all plots that have features
    plots = conn.execute(text("""
        SELECT 
            p.id, 
            ST_Y(ST_Centroid(ST_Transform(p.geometry, 4326))) AS lat, 
            ST_X(ST_Centroid(ST_Transform(p.geometry, 4326))) AS lon, 
            pf.land_use_type 
        FROM plots p
        JOIN plot_features pf ON p.id = pf.plot_id
    """)).mappings().fetchall()
    
    print(f"Found {len(plots)} plots in the database.")
    
    updated_count = 0
    for plot in plots:
        # Query real soil data for this plot's location
        soil_query = text("""
            SELECT * 
            FROM ldd_soil_group 
            WHERE ST_Intersects(geometry, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))
            LIMIT 1;
        """)
        
        soil_result = conn.execute(soil_query, {"lon": plot['lon'], "lat": plot['lat']}).mappings().fetchone()
        
        if soil_result:
            row = dict(soil_result)
            soil_desc = "Unknown"
            for col, val in row.items():
                col_upper = col.upper()
                if "GROUP" in col_upper or "DESC" in col_upper or "SERIE" in col_upper or "NAME" in col_upper:
                    if val:
                        soil_desc = str(val)
                        break
            
            water_cap = 0.30
            row_str = str(row).lower()
            if any(k in row_str for k in ["clay", "เหนียว", "poorly drained", "เลว"]):
                water_cap = 0.45
            elif any(k in row_str for k in ["sand", "ทราย", "well drained", "ดี"]):
                water_cap = 0.15
            else:
                import re
                match = re.search(r'\d+', soil_desc)
                if match:
                    code = int(match.group())
                    if 1 <= code <= 31:
                        water_cap = 0.45
                    elif code >= 32:
                        water_cap = 0.15
                elif "w" in soil_desc.lower():
                    water_cap = 0.45
                
            # Update the plot_features table
            update_query = text("""
                UPDATE plot_features
                SET land_use_type = :new_soil,
                    soil_water_capacity = :new_cap
                WHERE plot_id = :pid
            """)
            
            conn.execute(update_query, {
                "new_soil": soil_desc,
                "new_cap": water_cap,
                "pid": plot['id']
            })
            conn.commit()
            updated_count += 1
            print(f"Plot {plot['id']} updated: {plot['land_use_type']} -> {soil_desc}")
            
print(f"✅ Successfully updated {updated_count} old plots with REAL LDD data!")
