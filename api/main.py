import sys
import os
from pathlib import Path

# Add MyCane_BE/ root and api/ to sys.path for direct `python api/main.py` runs.
# MyCane_DB is no longer on sys.path — pipeline runs independently.
sys.path.append(str(Path(__file__).parent.parent))
sys.path.append(str(Path(__file__).parent))

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.db_pool import get_raw_db
get_db = get_raw_db  # alias for test dependency_overrides that reference api.main.get_db

from app.api.auth import router as auth_router
from app.api.plots import router as plots_router
from app.api.notifications import router as notifications_router
from app.api.features import router as features_router
from app.api.hotspots import router as hotspots_router
from app.api.tiles import router as tiles_router
from app.lifespan import lifespan

app = FastAPI(
    title="Satellite Team API",
    description="API สำหรับส่งมอบ Geo-features ให้กับ AI Team และ App Team",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(GZipMiddleware, minimum_size=1000)

app.include_router(auth_router,          prefix="/api/v1/auth",          tags=["Authentication"])
app.include_router(plots_router,         prefix="/api/v1/plots",         tags=["Plot Management"])
app.include_router(notifications_router, prefix="/api/v1/notifications", tags=["Notifications"])
app.include_router(features_router,      prefix="/api/v1",               tags=["Geo Features"])
app.include_router(hotspots_router,      prefix="/api/v1",               tags=["Hotspots"])
app.include_router(tiles_router,         prefix="/api/v1",               tags=["Tiles"])

@app.get("/api/v1/health", tags=["Health"])
def health_check():
    return {"status": "ok", "message": "Heaven Eye Backend is running on FastAPI"}

@app.get("/")
def read_root():
    return {"message": "Welcome to Satellite Team API", "status": "running"}

os.makedirs(Path(__file__).parent / "static" / "uploads", exist_ok=True)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=7860, reload=False)
