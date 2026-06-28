from datetime import datetime, timedelta
from typing import Optional
import jwt
import bcrypt
import os
import secrets
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from loguru import logger
from app.core.config import get_db
# We will import User model later when checking DB in dependencies

# JWT signing key. It MUST be supplied via the SECRET_KEY env var in any shared or
# deployed environment (e.g. the Hugging Face Space secrets). A hardcoded default
# would let anyone who reads the source forge a token for any user, so when the env
# var is missing we fall back to a random per-process key instead of a known string.
# Trade-off: the random fallback invalidates all issued JWTs on restart — set
# SECRET_KEY to keep sessions stable AND prevent token forgery.
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    SECRET_KEY = secrets.token_urlsafe(48)
    logger.warning(
        "SECRET_KEY env var is not set — using a random per-process key. JWTs will be "
        "invalidated on every restart. Set SECRET_KEY (HF Space secrets / .env) to keep "
        "sessions stable and prevent token forgery."
    )
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7 # 7 days

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

def get_password_hash(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

# We will define get_current_user in the auth.py or here after we define schemas
