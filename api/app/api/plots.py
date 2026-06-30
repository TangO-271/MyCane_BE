from fastapi import APIRouter, Depends, HTTPException, status, File, UploadFile, Request
from sqlalchemy.orm import Session
from app.core.config import get_db
from app.api.auth import get_current_user
from app.models.domain import User, Plot
from app.schemas.domain import PlotCreate, PlotResponse, PlotUpdate
import json
import os
import uuid

from app.docs.descriptions import PLOT_CREATE_DESC, PLOT_GET_DESC, PLOT_DELETE_DESC

router = APIRouter()

@router.post(
    "/", 
    response_model=PlotResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new farmland plot",
    description=PLOT_CREATE_DESC,
    responses={
        status.HTTP_201_CREATED: {
            "description": "Farmland plot successfully registered and area calculated."
        },
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Invalid, expired, or missing JWT credentials.",
            "content": {"application/json": {"example": {"detail": "Could not validate credentials"}}}
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "description": "Spatial query computation or database execution failed.",
            "content": {"application/json": {"example": {"detail": "Internal PostGIS ST_Transform error details..."}}}
        }
    }
)
def create_plot(plot: PlotCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Register farmland boundary:

    - **plot_name**: User-defined custom nickname for the farm plot
    - **geojson**: Standard GeoJSON Geometry polygon structure (WGS84)
    """
    try:
        from sqlalchemy import func

        # Build the UTM geometry expression once so we can reuse it
        utm_geom = func.ST_Transform(func.ST_GeomFromGeoJSON(json.dumps(plot.geojson)), 32647)

        # Calculate area in square metres using the UTM projection (EPSG:32647)
        from sqlalchemy import select as sa_select
        area_m2 = db.execute(
            sa_select(func.ST_Area(utm_geom))
        ).scalar()

        new_plot = Plot(
            user_id=current_user.id,
            plot_name=plot.plot_name,
            geometry=utm_geom,
            area_size=area_m2,          # ✅ persist computed area
            # Sugarcane (อ้อย) is the default crop across all five AOI provinces
            # (CLAUDE.md). Honour a crop the client supplied, else default to อ้อย.
            crop=plot.crop or "อ้อย",
            address=plot.address,
        )
        db.add(new_plot)
        db.commit()
        db.refresh(new_plot)

        # Fetch back with geometry converted to GeoJSON for the response
        result = db.query(
            Plot.id,
            Plot.user_id,
            Plot.plot_name,
            Plot.area_size,
            Plot.image_url,
            Plot.crop,
            Plot.address,
            func.ST_AsGeoJSON(func.ST_Transform(Plot.geometry, 4326)).label('geojson')
        ).filter(Plot.id == new_plot.id).first()

        return PlotResponse(
            id=result.id,
            user_id=result.user_id,
            plot_name=result.plot_name,
            area_size=result.area_size,
            image_url=result.image_url,
            crop=result.crop,
            address=result.address,
            geojson=json.loads(result.geojson)
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/", 
    response_model=list[PlotResponse],
    status_code=status.HTTP_200_OK,
    summary="Retrieve all registered farmland plots",
    description=PLOT_GET_DESC,
    responses={
        status.HTTP_200_OK: {
            "description": "List of user's registered plots successfully retrieved."
        },
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Invalid, expired, or missing JWT credentials.",
            "content": {"application/json": {"example": {"detail": "Could not validate credentials"}}}
        }
    }
)
def get_plots(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Get user plot boundary list.

    Requires valid **Authorization: Bearer <token>** authentication header.
    """
    from sqlalchemy import func
    plots = db.query(
        Plot.id, 
        Plot.user_id, 
        Plot.plot_name, 
        Plot.area_size, 
        Plot.image_url,
        Plot.crop,
        Plot.address,
        func.ST_AsGeoJSON(func.ST_Transform(Plot.geometry, 4326)).label('geojson')
    ).filter(Plot.user_id == current_user.id).all()
    
    return [
        PlotResponse(
            id=p.id,
            user_id=p.user_id,
            plot_name=p.plot_name,
            area_size=p.area_size,
            image_url=p.image_url,
            crop=p.crop,
            address=p.address,
            geojson=json.loads(p.geojson) if p.geojson else {}
        ) for p in plots
    ]

@router.delete(
    "/{plot_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a farmland plot",
    description=PLOT_DELETE_DESC,
    responses={
        status.HTTP_200_OK: {
            "description": "Farmland plot boundary deleted successfully.",
            "content": {"application/json": {"example": {"message": "Plot deleted successfully"}}}
        },
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Invalid, expired, or missing JWT credentials.",
            "content": {"application/json": {"example": {"detail": "Could not validate credentials"}}}
        },
        status.HTTP_404_NOT_FOUND: {
            "description": "Plot not found or doesn't belong to the authenticated user.",
            "content": {"application/json": {"example": {"detail": "Plot not found"}}}
        }
    }
)
def delete_plot(plot_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Delete farmland boundary by identifier:

    - **plot_id**: Database index of the target plot
    """
    plot = db.query(Plot).filter(Plot.id == plot_id, Plot.user_id == current_user.id).first()
    if not plot:
        raise HTTPException(status_code=404, detail="Plot not found")
    
    db.delete(plot)
    db.commit()
    return {"message": "Plot deleted successfully"}


@router.post(
    "/{plot_id}/upload-image",
    response_model=PlotResponse,
    status_code=status.HTTP_200_OK,
    summary="Upload plot photograph",
    description="Accepts multipart/form-data image files, saves them securely locally, and returns the updated plot details.",
    responses={
        status.HTTP_200_OK: {
            "description": "Image successfully uploaded and associated with plot."
        },
        status.HTTP_400_BAD_REQUEST: {
            "description": "Unsupported file format or too large file size."
        },
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Invalid, expired, or missing JWT credentials."
        },
        status.HTTP_404_NOT_FOUND: {
            "description": "Plot not found or doesn't belong to the current authenticated user."
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "description": "File system write error or database commit failure."
        }
    }
)
async def upload_plot_image(
    plot_id: int,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Verify plot exists and belongs to user
    plot = db.query(Plot).filter(Plot.id == plot_id, Plot.user_id == current_user.id).first()
    if not plot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plot not found")

    # Validate file type (only standard images)
    ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file type '{file.content_type}'. Supported image formats: JPEG, PNG, WEBP."
        )

    # Validate file size (max 10MB)
    MAX_FILE_SIZE = 10 * 1024 * 1024 # 10MB
    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File size exceeds the 10MB limit."
        )
    await file.seek(0)

    # Prepare file naming and directories
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        # fall back to map mime type to extension
        mime_map = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
        ext = mime_map.get(file.content_type, ".jpg")

    unique_id = uuid.uuid4().hex[:8]
    filename = f"plot_{plot_id}_{unique_id}{ext}"

    try:
        from app.core.storage import upload_file_to_storage
        
        # Generator for the fallback local URL
        def local_url_generator(fn):
            base_url = str(request.base_url).rstrip("/")
            return f"{base_url}/static/uploads/{fn}"
            
        image_url = upload_file_to_storage(
            db=db,
            filename=filename,
            contents=contents,
            content_type=file.content_type,
            fallback_url_generator=local_url_generator
        )

        # Update the plot and commit to DB
        plot.image_url = image_url
        db.commit()
        db.refresh(plot)

        # Fetch back with geometry converted to GeoJSON for the response
        from sqlalchemy import func
        result = db.query(
            Plot.id,
            Plot.user_id,
            Plot.plot_name,
            Plot.area_size,
            Plot.image_url,
            Plot.crop,
            Plot.address,
            func.ST_AsGeoJSON(func.ST_Transform(Plot.geometry, 4326)).label('geojson')
        ).filter(Plot.id == plot.id).first()

        return PlotResponse(
            id=result.id,
            user_id=result.user_id,
            plot_name=result.plot_name,
            area_size=result.area_size,
            image_url=result.image_url,
            crop=result.crop,
            address=result.address,
            geojson=json.loads(result.geojson) if result.geojson else {}
        )
    except Exception as e:
        db.rollback()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process and upload image: {str(e)}"
        )


@router.patch(
    "/{plot_id}",
    response_model=PlotResponse,
    status_code=status.HTTP_200_OK,
    summary="Update farmland plot details",
    description="Updates a plot's custom nickname, crop type, location address, and/or polygon coordinates."
)
def update_plot(
    plot_id: int,
    plot_update: PlotUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        # Verify plot exists and belongs to current user
        plot = db.query(Plot).filter(Plot.id == plot_id, Plot.user_id == current_user.id).first()
        if not plot:
            raise HTTPException(status_code=404, detail="Plot not found")
            
        if plot_update.plot_name is not None:
            plot.plot_name = plot_update.plot_name
        if plot_update.crop is not None:
            plot.crop = plot_update.crop
        if plot_update.address is not None:
            plot.address = plot_update.address
            
        if plot_update.geojson is not None:
            from sqlalchemy import func
            utm_geom = func.ST_Transform(func.ST_GeomFromGeoJSON(json.dumps(plot_update.geojson)), 32647)
            # Re-calculate area in square metres using PostGIS UTM Zone 47N projection
            area_m2 = db.execute(func.ST_Area(utm_geom).select()).scalar()
            plot.geometry = utm_geom
            plot.area_size = area_m2
            
        db.commit()
        db.refresh(plot)
        
        # Fetch back with geometry converted to GeoJSON for the response
        from sqlalchemy import func
        result = db.query(
            Plot.id,
            Plot.user_id,
            Plot.plot_name,
            Plot.area_size,
            Plot.image_url,
            Plot.crop,
            Plot.address,
            func.ST_AsGeoJSON(func.ST_Transform(Plot.geometry, 4326)).label('geojson')
        ).filter(Plot.id == plot.id).first()
        
        return PlotResponse(
            id=result.id,
            user_id=result.user_id,
            plot_name=result.plot_name,
            area_size=result.area_size,
            image_url=result.image_url,
            crop=result.crop,
            address=result.address,
            geojson=json.loads(result.geojson) if result.geojson else {}
        )
    except Exception as e:
        db.rollback()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))

