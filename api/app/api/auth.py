from fastapi import APIRouter, Depends, HTTPException, status, Header, File, UploadFile, Request
from sqlalchemy.orm import Session
from fastapi.security import OAuth2PasswordRequestForm
from app.core.config import get_db
from app.core.security import (
    get_password_hash,
    verify_password,
    create_access_token,
    oauth2_scheme,
    ALGORITHM,
    SECRET_KEY,
    ACCESS_TOKEN_EXPIRE_MINUTES,
)
from app.models.domain import User
from app.schemas.domain import UserCreate, UserResponse, Token, LineLoginRequest, UserUpdate
from app.services import line_client
from loguru import logger
import jwt
import uuid
import os
from datetime import timedelta
from typing import Optional

from app.docs.descriptions import AUTH_REGISTER_DESC, AUTH_LOGIN_DESC, AUTH_ME_DESC

# Issue tokens with the intended lifetime (config says 7 days). Previously login() omitted
# expires_delta, so create_access_token defaulted to 15 minutes — sessions expired fast.
ACCESS_TOKEN_TTL = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

router = APIRouter()

def get_current_user(
    x_app_authorization: Optional[str] = Header(None, alias="X-App-Authorization"),
    token: str = Depends(oauth2_scheme), 
    db: Session = Depends(get_db)
):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    final_token = token
    if x_app_authorization:
        if x_app_authorization.lower().startswith("bearer "):
            final_token = x_app_authorization[7:]
        else:
            final_token = x_app_authorization
            
    try:
        payload = jwt.decode(final_token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception
    
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise credentials_exception
    return user

@router.post(
    "/register", 
    response_model=UserResponse, 
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
    description=AUTH_REGISTER_DESC,
    responses={
        status.HTTP_201_CREATED: {
            "description": "User created successfully."
        },
        status.HTTP_400_BAD_REQUEST: {
            "description": "Registration failed due to existing email.",
            "content": {"application/json": {"example": {"detail": "Email already registered"}}}
        }
    }
)
def register_user(user: UserCreate, db: Session = Depends(get_db)):
    """
    Register a new user account.

    - **name**: User's full name
    - **email**: Unique email address (validated format)
    - **password**: Safe plain text password (hashed automatically)
    """
    db_user = db.query(User).filter(User.email == user.email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    hashed_password = get_password_hash(user.password)
    new_user = User(
        name=user.name,
        email=user.email,
        hashed_password=hashed_password
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user

@router.post(
    "/login", 
    response_model=Token,
    status_code=status.HTTP_200_OK,
    summary="Authenticate and obtain JWT token",
    description=AUTH_LOGIN_DESC,
    responses={
        status.HTTP_200_OK: {
            "description": "Authentication successful. Access token returned."
        },
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Incorrect email or password.",
            "content": {"application/json": {"example": {"detail": "Incorrect email or password"}}}
        }
    }
)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """
    Authenticate a user.

    - **username**: User's email address
    - **password**: Plain text password to verify
    """
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token = create_access_token(data={"sub": user.email}, expires_delta=ACCESS_TOKEN_TTL)
    return {"access_token": access_token, "token_type": "bearer"}


@router.post(
    "/line-login",
    response_model=Token,
    status_code=status.HTTP_200_OK,
    summary="Authenticate via LINE Login (LIFF) and obtain a JWT",
    description=(
        "Maps a LINE identity to a backend account (CLAUDE.md §LINE / docs/line-architecture.md §2). "
        "On first login the account is auto-created with a synthetic email "
        "(line_<userId>@line.tasawan.app) and a random password; subsequent logins reuse it. "
        "Returns the same bearer token shape as /login so the FE stores it identically."
    ),
    responses={
        status.HTTP_200_OK: {"description": "LINE user authenticated; access token returned."},
    },
)
def line_login(req: LineLoginRequest, db: Session = Depends(get_db)):
    """Find-or-create a user by LINE userId, then issue an app JWT."""
    # Security: when LINE_LOGIN_CHANNEL_ID is configured, trust ONLY a LINE-verified
    # identity — the client-sent line_user_id is spoofable. Verify the id_token with
    # LINE and use the verified userId. When not configured, fall back to the
    # (hackathon-grade) client-sent id with a warning. See CLAUDE.md LINE hardening TODO.
    if line_client.id_token_verification_enabled():
        line_user_id = line_client.verify_id_token(req.id_token)
        if not line_user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing LINE id_token",
            )
    else:
        logger.warning(
            "LINE login is trusting a client-sent userId (LINE_LOGIN_CHANNEL_ID not set). "
            "Set it and send liff.getIDToken() to verify LINE identities server-side."
        )
        line_user_id = req.line_user_id

    user = db.query(User).filter(User.line_user_id == line_user_id).first()
    if not user:
        # Synthetic email on a registrable domain (the email validator rejects reserved TLDs
        # like .local). Random password — the user authenticates via LINE, not this password.
        synthetic_email = f"line_{line_user_id}@line.tasawan.app"
        user = User(
            name=req.display_name or "LINE User",
            email=synthetic_email,
            hashed_password=get_password_hash(uuid.uuid4().hex),
            line_user_id=line_user_id,
            role="farmer",
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    access_token = create_access_token(data={"sub": user.email}, expires_delta=ACCESS_TOKEN_TTL)
    return {"access_token": access_token, "token_type": "bearer"}

@router.get(
    "/me", 
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve current user profile",
    description=AUTH_ME_DESC,
    responses={
        status.HTTP_200_OK: {
            "description": "Current user profile fetched successfully."
        },
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Invalid, expired, or missing JWT credentials.",
            "content": {"application/json": {"example": {"detail": "Could not validate credentials"}}}
        }
    }
)
def read_users_me(current_user: User = Depends(get_current_user)):
    """
    Get active session profile.

    Requires a valid **Authorization: Bearer <token>** header.
    """
    return current_user


@router.post(
    "/me/upload-image",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Upload user profile photograph",
    description="Accepts multipart/form-data image files, saves them securely locally, and returns the updated user profile details.",
    responses={
        status.HTTP_200_OK: {
            "description": "Image successfully uploaded and associated with user profile."
        },
        status.HTTP_400_BAD_REQUEST: {
            "description": "Unsupported file format or too large file size."
        },
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Invalid, expired, or missing JWT credentials."
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "description": "File system write error or database commit failure."
        }
    }
)
async def upload_profile_image(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
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
    filename = f"profile_{current_user.id}_{unique_id}{ext}"

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

        # Update the user and commit to DB
        current_user.profile_image_url = image_url
        db.commit()
        db.refresh(current_user)

        return current_user
    except Exception as e:
        db.rollback()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process and upload profile image: {str(e)}"
        )

# In auth.py router — add this endpoint
@router.patch(
    "/me",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Update current user profile",
    description="Updates editable profile fields (name, phone). Partial update — omitted fields are left unchanged.",
)
def update_users_me(
    payload: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if payload.name is not None:
        current_user.name = payload.name
    if payload.phone is not None:
        current_user.phone = payload.phone
    db.commit()
    db.refresh(current_user)
    return current_user