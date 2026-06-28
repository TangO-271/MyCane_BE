import sys
import psycopg2
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from pipeline.config import DATABASE_URL as DB_URL

def main():
    print(f"Connecting to {DB_URL.split('@')[1]}...")
    try:
        conn = psycopg2.connect(DB_URL)
        conn.autocommit = True
        with conn.cursor() as cur:
            print("Executing ALTER TABLE to add profile_image_url column...")
            cur.execute("""
                ALTER TABLE users 
                ADD COLUMN IF NOT EXISTS profile_image_url VARCHAR(512);
            """)
            print("Successfully added profile_image_url column to users table!")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if 'conn' in locals() and conn:
            conn.close()

if __name__ == "__main__":
    main()
