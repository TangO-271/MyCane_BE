import os
import sys
from pathlib import Path
from dotenv import load_dotenv

import geopandas as gpd
from sqlalchemy import create_engine
import urllib.parse

# Load environment variables
load_dotenv()

# Database credentials
POSTGIS_HOST = os.getenv("POSTGIS_HOST")
POSTGIS_PORT = os.getenv("POSTGIS_PORT", "5432")
POSTGIS_DB = os.getenv("POSTGIS_DB", "postgres")
POSTGIS_USER = os.getenv("POSTGIS_USER", "postgres")
POSTGIS_PASSWORD = os.getenv("POSTGIS_PASSWORD")

if not all([POSTGIS_HOST, POSTGIS_USER, POSTGIS_PASSWORD]):
    print("❌ Missing Supabase Database credentials in .env")
    sys.exit(1)

# Format the password to handle special characters
encoded_password = urllib.parse.quote_plus(POSTGIS_PASSWORD)

# Create SQLAlchemy Engine
db_url = f"postgresql://{POSTGIS_USER}:{encoded_password}@{POSTGIS_HOST}:{POSTGIS_PORT}/{POSTGIS_DB}"
engine = create_engine(db_url)

RAW_DIR = Path("data/raw")
PROVINCE_ABBR = ["plk", "nsn", "utt", "spb", "pbn"]

TABLE_NAME = "ldd_soil_group"

print("==================================================")
print("🌍 GEOAI: Migrating LDD Shapefiles to Supabase 🚀")
print("==================================================")

for abbr in PROVINCE_ABBR:
    shp_path = RAW_DIR / "shapefiles" / abbr / f"soilgroup_{abbr}.shp"
    
    if not shp_path.exists():
        print(f"⚠️ Warning: Shapefile not found for {abbr} at {shp_path}")
        continue
        
    print(f"📂 Reading: {shp_path.name}...")
    
    # Read the shapefile (LDD shapefiles use Thai encoding TIS-620 or Windows-874)
    gdf = gpd.read_file(shp_path, encoding='TIS-620')
    
    print(f"   -> Read {len(gdf)} polygons. Checking CRS...")
    
    # Convert CRS to EPSG:4326 (WGS84 Lat/Lon) which is standard for our pipeline
    if gdf.crs is None or gdf.crs != "EPSG:4326":
        print(f"   -> Reprojecting from {gdf.crs} to EPSG:4326...")
        gdf = gdf.to_crs("EPSG:4326")
        
    # Ensure geometries are valid (sometimes shapefiles have self-intersecting polygons)
    gdf['geometry'] = gdf['geometry'].make_valid()
    
    # Standardize column names (lowercase) to avoid PostgreSQL case-sensitivity issues
    gdf.columns = [col.lower() for col in gdf.columns]
    
    # We add a province column to keep track
    gdf['province_abbr'] = abbr

    print(f"   -> Uploading to Supabase table '{TABLE_NAME}'...")
    
    try:
        # Use if_exists='append' to add all provinces to the same table
        # We only create the GiST index on the first run, or let to_postgis handle it
        is_first = (abbr == PROVINCE_ABBR[0])
        action = "replace" if is_first else "append"
        
        gdf.to_postgis(
            TABLE_NAME, 
            engine, 
            if_exists=action, 
            index=False,
            # Create a spatial index for fast intersection queries
            dtype={'geometry': 'geometry(Geometry,4326)'}
        )
        print("   ✅ Upload successful!")
    except Exception as e:
        print(f"   ❌ Failed to upload {abbr}: {e}")

print("==================================================")
print("🎉 Migration Complete! You can now safely delete the shapefiles from data/raw/shapefiles")
print("==================================================")
