import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

# Load .env from the config folder or root folder
BASE_DIR = Path(__file__).resolve().parent.parent.parent
env_path = BASE_DIR / "config" / ".env"
if env_path.exists():
    load_dotenv(env_path)
else:
    load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/heavenseye")
# SQLAlchemy 2.0 requires postgresql:// instead of postgres://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Supabase's session-mode pooler caps total clients (default 15). The BE opens
# connections from TWO places against the same pooler — this SQLAlchemy engine AND
# the psycopg2 pool (app/core/db_pool.py, sized in app/lifespan.py). Their combined
# max must stay under that limit or new connections fail with
# "max clients reached in session mode". Budget: SQLAlchemy ≤5 + psycopg2 ≤8 = 13.
#   - pool_pre_ping: drop connections Supabase closed while idle (avoids stale-conn errors)
#   - pool_recycle: proactively recycle before the pooler's idle timeout
engine = create_engine(
    DATABASE_URL,
    pool_size=int(os.getenv("SQLALCHEMY_POOL_SIZE", "3")),
    max_overflow=int(os.getenv("SQLALCHEMY_MAX_OVERFLOW", "2")),
    pool_pre_ping=True,
    pool_recycle=1800,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
