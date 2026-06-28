import os, urllib.parse
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()
engine = create_engine(f"postgresql://{os.getenv('POSTGIS_USER')}:{urllib.parse.quote_plus(os.getenv('POSTGIS_PASSWORD'))}@{os.getenv('POSTGIS_HOST')}:{os.getenv('POSTGIS_PORT')}/{os.getenv('POSTGIS_DB')}")

with engine.connect() as conn:
    row = conn.execute(text("SELECT * FROM ldd_soil_group LIMIT 1")).mappings().fetchone()
    print("Row columns and values:")
    for k, v in row.items():
        if k != "geometry":
            print(f"  {k}: {v}")
