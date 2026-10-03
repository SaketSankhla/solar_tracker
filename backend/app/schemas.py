"""Pydantic schemas for API requests, responses, and MQTT payloads."""

from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, field_validator

class TelemetryPayload(BaseModel):
    """Schema for incoming telemetry data."""
    schema_version: int
    device_id: str
    seq: int
    uptime_ms: int
    voltage_v: float
    current_a: float
    power_w: float
    panel_angle_deg: float
    motor_steps: int
    tept_top: int
    tept_bottom: int
    tept_left: int
    tept_right: int
    ina219_ok: bool
    sensors_ok: bool
    mode: str

    @field_validator("schema_version")
    def check_schema_version(cls, v):
        if v != 1:
            raise ValueError(f"Unsupported schema version: {v}")
        return v


class StatusPayload(BaseModel):
    device_id: str
    state: str
    firmware_version: str
    uptime_ms: int
    ip_address: Optional[str] = None
    free_heap: Optional[int] = None


class MotorStatePayload(BaseModel):
    mode: str
    motor_enabled: bool
    motor_moving: bool
    panel_angle_deg: float
    motor_steps: int
    target_angle_deg: Optional[float] = None


class CommandAckPayload(BaseModel):
    cmd_id: str
    seq: int
    uptime_ms: int
    status: str
    reason: Optional[str] = None


class AlertPayload(BaseModel):
    seq: int
    uptime_ms: int
    alert_id: str
    severity: str
    code: str
    message: str


class CommandRequest(BaseModel):
    command: str
    params: Optional[Dict[str, Any]] = None


class CommandResponse(BaseModel):
    cmd_id: str
    command: str
    params: Optional[Dict[str, Any]] = None
    status: str
    created_at: datetime
    expires_at: datetime


class LiveStateResponse(BaseModel):
    telemetry: Optional[TelemetryPayload] = None
    solar_state: Optional[Dict[str, float]] = None


class HealthResponse(BaseModel):
    mqtt_connected: bool
    db_ok: bool
    last_telemetry_age_sec: Optional[float] = None
    device_id: str


class AnalyticsResponse(BaseModel):
    period: str
    total_energy_wh: float
    avg_power_w: float
    peak_power_w: float
    avg_tracking_error_deg: float
    fault_count: int
