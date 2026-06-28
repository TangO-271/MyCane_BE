import sys
import psycopg2
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from pipeline.config import DATABASE_URL as DB_URL

def main():
    try:
        conn = psycopg2.connect(DB_URL)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT column_name, data_type 
                FROM information_schema.columns 
                WHERE table_name = 'plots';
            """)
            cols = cur.fetchall()
            print("Columns in 'plots' table:")
            for c in cols:
                print(f" - {c[0]}: {c[1]}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
