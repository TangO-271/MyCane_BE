from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from geoalchemy2 import Geometry
from app.core.config import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(50), default="farmer")
    subscription_tier = Column(String(50), default="free")
    line_user_id = Column(String(255), nullable=True)
    profile_image_url = Column(Text, nullable=True)
    phone = Column(String(20), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    plots = relationship("Plot", back_populates="owner")

class Plot(Base):
    __tablename__ = "plots"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    plot_name = Column(String(255))
    area_size = Column(Float)
    geometry = Column(Geometry(geometry_type='POLYGON', srid=32647))
    image_url = Column(Text, nullable=True)
    crop = Column(String(255), nullable=True)
    address = Column(String(255), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    owner = relationship("User", back_populates="plots")

class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    plot_id = Column(Integer, ForeignKey("plots.id", ondelete="SET NULL"), nullable=True)
    title = Column(String(255), nullable=True)
    message = Column(Text, nullable=False)
    hazard_type = Column(String(20), nullable=True)   # fire | flood | drought | disease | system
    severity = Column(String(20), default="info")     # danger | warn | ok | info
    is_read = Column(Boolean, default=False)
    channels = Column(String(120), nullable=True)     # comma-joined dispatched channels
    dedupe_key = Column(String(255), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
