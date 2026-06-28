import os
import requests
import base64
from sqlalchemy.orm import Session
from sqlalchemy import text
from fastapi import HTTPException, status

def get_supabase_config():
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")
    bucket_name = os.getenv("SUPABASE_STORAGE_BUCKET", "profiles")
    
    if not supabase_url:
        db_url = os.getenv("DATABASE_URL", "")
        # Parse the project ref from database username: postgres.<project_ref>
        if "postgres." in db_url:
            try:
                username_part = db_url.split("://")[1].split("@")[0].split(":")[0]
                if "postgres." in username_part:
                    project_id = username_part.split("postgres.")[1]
                    supabase_url = f"https://{project_id}.supabase.co"
            except Exception:
                pass
                
    return supabase_url, supabase_key, bucket_name

def ensure_supabase_bucket(db: Session, bucket_name: str):
    try:
        # Check if bucket exists
        res = db.execute(text("SELECT id FROM storage.buckets WHERE id = :name"), {"name": bucket_name}).first()
        if not res:
            # Create the bucket (public: true)
            db.execute(
                text("INSERT INTO storage.buckets (id, name, public) VALUES (:name, :name, true)"),
                {"name": bucket_name}
            )
            db.commit()
    except Exception:
        db.rollback()

def upload_file_to_storage(db: Session, filename: str, contents: bytes, content_type: str, fallback_url_generator) -> str:
    """
    Uploads a file to Supabase Storage if configured via SUPABASE_KEY.
    Otherwise, encodes the file as a base64 Data URL and stores it directly in the Supabase PostgreSQL database column!
    This provides persistent, stateless, zero-configuration database-backed storage out of the box.
    """
    supabase_url, supabase_key, bucket_name = get_supabase_config()
    
    if supabase_url and supabase_key:
        # Ensure the bucket exists
        ensure_supabase_bucket(db, bucket_name)
        
        upload_url = f"{supabase_url}/storage/v1/object/{bucket_name}/{filename}"
        headers = {
            "Authorization": f"Bearer {supabase_key}",
            "ApiKey": supabase_key,
            "Content-Type": content_type
        }
        
        try:
            resp = requests.post(upload_url, headers=headers, data=contents, timeout=15)
            if resp.status_code == 200:
                # Return the public Supabase URL
                return f"{supabase_url}/storage/v1/object/public/{bucket_name}/{filename}"
            elif resp.status_code == 409:
                # 409 Conflict: file already exists. We can still return the public URL.
                return f"{supabase_url}/storage/v1/object/public/{bucket_name}/{filename}"
            else:
                # Fall back to base64 encoding rather than hard failure
                pass
        except Exception:
            # Fall back to base64 encoding on connection errors
            pass
            
    # Convert file bytes directly to base64 Data URL for zero-configuration Supabase Database storage
    try:
        base64_data = base64.b64encode(contents).decode("utf-8")
        return f"data:{content_type};base64,{base64_data}"
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to encode file as base64: {str(e)}"
        )
