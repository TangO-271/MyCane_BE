import sys
import psycopg2
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from pipeline.config import DATABASE_URL as DB_URL
SQL_FILE = Path(__file__).parent / "init.sql"

def main():
    print(f"Connecting to {DB_URL.split('@')[1]}...")
    try:
        conn = psycopg2.connect(DB_URL)
        conn.autocommit = True
        with conn.cursor() as cur:
            with open(SQL_FILE, 'r', encoding='utf-8') as f:
                sql = f.read()
            
            print("Executing init.sql...")
            # แยกคำสั่งด้วย ; เพื่อให้รันทีละอัน ป้องกัน error tables already exist
            statements = sql.split(';')
            for stmt in statements:
                stmt = stmt.strip()
                if not stmt: continue
                try:
                    cur.execute(stmt)
                except psycopg2.errors.DuplicateTable:
                    print(f"Skipping: Table already exists")
                except psycopg2.errors.DuplicateObject:
                    print(f"Skipping: Object/Index already exists")
                except Exception as e:
                    print(f"Error on statement: {e}")
            
            print("Successfully applied init.sql to Supabase!")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if 'conn' in locals() and conn:
            conn.close()

if __name__ == "__main__":
    main()
