from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

# User Schemas
class UserBase(BaseModel):
    name: str = Field(
        ..., 
        description="Full name of the user registered in the system", 
        example="John Doe"
    )
    email: EmailStr = Field(
        ..., 
        description="Unique email address used for login and notifications", 
        example="john.doe@example.com"
    )

class UserCreate(UserBase):
    password: str = Field(
        ..., 
        description="Secure user password, must contain at least 8 characters", 
        min_length=8, 
        example="P@ssw0rd123!"
    )

class UserResponse(UserBase):
    id: int = Field(
        ..., 
        description="Unique auto-incremented database identifier of the user", 
        example=1
    )
    role: str = Field(
        ..., 
        description="Access control role assigned to the user", 
        example="farmer"
    )
    subscription_tier: str = Field(
        ..., 
        description="Pricing subscription tier defining system usage and feature limits", 
        example="free"
    )
    line_user_id: Optional[str] = Field(          # ← ADD
        None,
        description="LINE userId linked to this account, populated on LINE login or manual link",
        example="U692a54c713f8bbb04bcf8edd923d3540"
    )
    phone: Optional[str] = Field(                 # ← ADD
        None,
        description="User's phone number, optional",
        example="+66 81 234 5678"
    )
    profile_image_url: Optional[str] = Field(
        None,
        description="Public URL or path accessing the uploaded user profile photograph",
        example="http://localhost:8000/static/uploads/profile_1.jpg"
    )
    created_at: datetime = Field(
        ..., 
        description="Timestamp of user account creation in UTC standard format", 
        example="2026-05-20T21:40:00Z"
    )

    class Config:
        from_attributes = True

class UserUpdate(BaseModel):
    name: Optional[str] = Field(None, example="สมชาย ใจดี")
    phone: Optional[str] = Field(None, example="+66 81 234 5678")

# Token Schemas
class Token(BaseModel):
    access_token: str = Field(
        ..., 
        description="JSON Web Token (JWT) authorizing access to protected endpoints", 
        example="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
    )
    token_type: str = Field(
        ..., 
        description="Format category of the authentication token", 
        example="bearer"
    )

class TokenData(BaseModel):
    email: Optional[str] = Field(
        None,
        description="Subject/Email identifier extracted from the decrypted JWT payload",
        example="john.doe@example.com"
    )

# LINE Login Schema
class LineLoginRequest(BaseModel):
    line_user_id: str = Field(
        ...,
        description="LINE userId returned by liff.getProfile() — the stable identity used to map LINE users to a backend account",
        example="U1234567890abcdef1234567890abcdef"
    )
    display_name: Optional[str] = Field(
        None,
        description="LINE display name from liff.getProfile(), used as the account name on first login",
        example="สมชาย ใจดี"
    )
    id_token: Optional[str] = Field(
        None,
        description=(
            "LINE ID token from liff.getIDToken(). Required only when the backend is "
            "configured to verify LINE identities (LINE_LOGIN_CHANNEL_ID env set): the "
            "server validates it with LINE and uses the verified userId, ignoring the "
            "spoofable client-sent line_user_id."
        ),
        example="eyJhbGciOiJIUzI1NiJ9..."
    )

# Notification Schemas
class NotificationResponse(BaseModel):
    id: int = Field(..., description="Unique notification identifier", example=42)
    plot_id: Optional[int] = Field(None, description="Related plot id, if the alert is plot-specific", example=101)
    title: Optional[str] = Field(None, description="Short alert headline", example="เตือนภัยไฟ — พบจุดความร้อนใกล้แปลง")
    message: str = Field(..., description="Full Thai alert body", example="พบจุดความร้อน 3 จุด ใกล้แปลงนาเหนือ ...")
    hazard_type: Optional[str] = Field(None, description="fire | flood | drought | disease | system", example="fire")
    severity: str = Field("info", description="danger | warn | ok | info", example="danger")
    is_read: bool = Field(False, description="Whether the user has read this notification", example=False)
    channels: Optional[str] = Field(None, description="Comma-joined channels actually dispatched", example="LINE,Firebase Push")
    created_at: datetime = Field(..., description="Creation timestamp (UTC)", example="2026-05-25T08:00:00Z")

    class Config:
        from_attributes = True

# Plot Schemas
class PlotCreate(BaseModel):
    plot_name: str = Field(
        ..., 
        description="Human-readable nickname/identifier for the agricultural land plot", 
        example="North Rice Field"
    )
    geojson: Dict[str, Any] = Field(
        ..., 
        description="Standard GeoJSON Geometry (POLYGON) representing the parcel's geographic boundaries in GPS coordinate system (WGS84, EPSG:4326)", 
        example={
            "type": "Polygon", 
            "coordinates": [
                [
                    [100.5, 13.7], 
                    [100.6, 13.7], 
                    [100.6, 13.8], 
                    [100.5, 13.8], 
                    [100.5, 13.7]
                ]
            ]
        }
    )
    crop: Optional[str] = Field(
        None,
        description="Type of crop planted (defaults to 'อ้อย' / sugarcane when omitted)",
        example="อ้อย"
    )
    address: Optional[str] = Field(
        None,
        description="Plot location / address",
        example="อ.เมือง จ.นครสวรรค์"
    )

class PlotUpdate(BaseModel):
    plot_name: Optional[str] = Field(None, description="Human-readable nickname/identifier")
    geojson: Optional[Dict[str, Any]] = Field(None, description="Standard GeoJSON Geometry (POLYGON)")
    crop: Optional[str] = Field(None, description="Type of crop planted")
    address: Optional[str] = Field(None, description="Plot location / address")

class PlotResponse(BaseModel):
    id: int = Field(
        ..., 
        description="Unique auto-incremented database identifier of the plot", 
        example=101
    )
    user_id: int = Field(
        ..., 
        description="Database identifier of the farmer owner", 
        example=1
    )
    plot_name: str = Field(
        ..., 
        description="Human-readable nickname/identifier for the farmland plot", 
        example="North Rice Field"
    )
    area_size: Optional[float] = Field(
        None, 
        description="Calculated area size in square meters, generated automatically using PostGIS UTM Zone 47N projection (EPSG:32647)", 
        example=15680.45
    )
    geojson: Dict[str, Any] = Field(
        ..., 
        description="Standard GeoJSON Geometry polygon representing boundaries in WGS84 coordinates", 
        example={
            "type": "Polygon", 
            "coordinates": [
                [
                    [100.5, 13.7], 
                    [100.6, 13.7], 
                    [100.6, 13.8], 
                    [100.5, 13.8], 
                    [100.5, 13.7]
                ]
            ]
        }
    )
    image_url: Optional[str] = Field(
        None, 
        description="Public URL or path accessing the uploaded plot photograph", 
        example="http://localhost:8000/static/uploads/plot_101.jpg"
    )
    crop: Optional[str] = Field(
        None,
        description="Type of crop planted",
        example="ข้าว (นาปี)"
    )
    address: Optional[str] = Field(
        None,
        description="Plot location / address",
        example="อ.เมือง จ.นครสวรรค์"
    )

    class Config:
        from_attributes = True

