"""SQLAlchemy ORM models for the Solar Tracker database."""

from datetime import datetime
from typing import Optional
from sqlalchemy import String, Float, Integer, Boolean, DateTime, ForeignKey, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass

class Device(Base):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    latitude: Mapped[float] = mapped_column(Float, default=0.0)
    longitude: Mapped[float] = mapped_column(Float, default=0.0)
    altitude: Mapped[float] = mapped_column(Float, default=0.0)
    timezone: Mapped[str] = mapped_column(String, default="UTC")
    firmware_version: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class Telemetry(Base):
    __tablename__ = "telemetry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String, ForeignKey("devices.id"))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    uptime_ms: Mapped[int] = mapped_column(Integer)
    voltage_v: Mapped[float] = mapped_column(Float)
    current_a: Mapped[float] = mapped_column(Float)
    power_w: Mapped[float] = mapped_column(Float)
    panel_angle_deg: Mapped[float] = mapped_column(Float)
    motor_steps: Mapped[int] = mapped_column(Integer)
    tept_top: Mapped[int] = mapped_column(Integer)
    tept_bottom: Mapped[int] = mapped_column(Integer)
    tept_left: Mapped[int] = mapped_column(Integer)
    tept_right: Mapped[int] = mapped_column(Integer)
    ina219_ok: Mapped[bool] = mapped_column(Boolean)
    sensors_ok: Mapped[bool] = mapped_column(Boolean)
    mode: Mapped[str] = mapped_column(String)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String, ForeignKey("devices.id"))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    event_type: Mapped[str] = mapped_column(String)  # 'warning' / 'error' / 'critical'
    code: Mapped[str] = mapped_column(String)
    message: Mapped[str] = mapped_column(String)


class Command(Base):
    __tablename__ = "commands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cmd_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    device_id: Mapped[str] = mapped_column(String, ForeignKey("devices.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    command: Mapped[str] = mapped_column(String)
    params: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending") # 'pending'/'accepted'/'completed'/'rejected'/'expired'/'duplicate'/'failed'
    ack_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class SolarState(Base):
    __tablename__ = "solar_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String, ForeignKey("devices.id"))
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sun_azimuth_deg: Mapped[float] = mapped_column(Float)
    sun_elevation_deg: Mapped[float] = mapped_column(Float)
    target_panel_angle_deg: Mapped[float] = mapped_column(Float)
    tracking_error_deg: Mapped[float] = mapped_column(Float)


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String, ForeignKey("devices.id"))
    predicted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    model_version: Mapped[str] = mapped_column(String)
    predicted_power_w: Mapped[float] = mapped_column(Float)
    measured_power_w: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    residual_w: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
