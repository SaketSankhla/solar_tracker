"""FastAPI routes for the backend API."""

import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, Security, Query, Request
from fastapi.security.api_key import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database import get_db
from app.config import get_settings, Settings
from app.schemas import (
    LiveStateResponse, HealthResponse, AnalyticsResponse,
    CommandRequest, CommandResponse
)
from app.models import Telemetry, Command, Event, SolarState
# Avoid circular imports by not directly importing mqtt client instance, pass via state or similar if needed.
# For simplicity, assuming a global client or dependency injection in real app.

router = APIRouter()
settings = get_settings()

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def verify_api_key(api_key_header: str = Security(api_key_header), settings: Settings = Depends(get_settings)):
    if api_key_header == settings.api_key:
        return api_key_header
    raise HTTPException(status_code=403, detail="Could not validate API KEY")

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except:
                pass

ws_manager = ConnectionManager()

# In-memory ultra-fast cache for latest telemetry (<0.1ms access)
latest_live_cache: dict[str, dict] = {}

@router.get("/api/live")
async def get_live_state(device_id: str = "tracker-01", db: AsyncSession = Depends(get_db)):
    """Get the latest telemetry data with sub-millisecond memory-first caching."""
    if device_id in latest_live_cache:
        cached = latest_live_cache[device_id]
        return {
            "telemetry": cached["telemetry"],
            "ai_insight": cached.get("ai_insight"),
            "solar_state": cached.get("solar_state"),
            "cached": True,
        }

    stmt = select(Telemetry).where(Telemetry.device_id == device_id).order_by(Telemetry.received_at.desc()).limit(1)
    result = await db.execute(stmt)
    latest = result.scalar_one_or_none()
    
    if not latest:
        return {"telemetry": None, "ai_insight": None, "solar_state": None, "cached": False}
        
    solar_stmt = select(SolarState).where(SolarState.device_id == device_id).order_by(SolarState.calculated_at.desc()).limit(1)
    solar_res = await db.execute(solar_stmt)
    latest_solar = solar_res.scalar_one_or_none()

    solar_data = None
    if latest_solar:
        solar_data = {
            "sun_azimuth_deg": latest_solar.sun_azimuth_deg,
            "sun_elevation_deg": latest_solar.sun_elevation_deg,
            "target_panel_angle_deg": latest_solar.target_panel_angle_deg,
            "tracking_error_deg": latest_solar.tracking_error_deg,
        }

    return {
        "telemetry": {
            "schema_version": 1,
            "device_id": latest.device_id,
            "seq": latest.seq,
            "uptime_ms": latest.uptime_ms,
            "voltage_v": latest.voltage_v,
            "current_a": latest.current_a,
            "power_w": latest.power_w,
            "panel_angle_deg": latest.panel_angle_deg,
            "motor_steps": latest.motor_steps,
            "tept_top": latest.tept_top,
            "tept_bottom": latest.tept_bottom,
            "tept_left": latest.tept_left,
            "tept_right": latest.tept_right,
            "ina219_ok": latest.ina219_ok,
            "sensors_ok": latest.sensors_ok,
            "mode": latest.mode
        },
        "ai_insight": None,
        "solar_state": solar_data,
        "cached": False,
    }

@router.post("/api/telemetry")
async def ingest_telemetry_direct(payload: dict):
    """Direct low-latency HTTP ingestion (alternative to MQTT)."""
    from app.schemas import TelemetryPayload
    from app.workers import telemetry_queue
    if "device_id" not in payload:
        payload["device_id"] = "tracker-01"
    validated = TelemetryPayload.model_validate(payload)
    await telemetry_queue.put_telemetry(validated)
    return {"status": "accepted", "seq": validated.seq}

@router.get("/api/history")
async def get_history(
    device_id: str = "tracker-01",
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    limit: int = Query(200, le=1000),
    db: AsyncSession = Depends(get_db)
):
    """Get historical telemetry data."""
    stmt = select(Telemetry).where(Telemetry.device_id == device_id)
    if start:
        stmt = stmt.where(Telemetry.received_at >= start)
    if end:
        stmt = stmt.where(Telemetry.received_at <= end)
        
    stmt = stmt.order_by(Telemetry.received_at.desc()).limit(limit)
    result = await db.execute(stmt)
    records = result.scalars().all()
    return records

@router.get("/api/health", response_model=HealthResponse)
async def get_health(request: Request, settings: Settings = Depends(get_settings), db: AsyncSession = Depends(get_db)):
    """Health check endpoint."""
    db_ok = True
    try:
        await db.execute(select(1))
    except Exception:
        db_ok = False
        
    stmt = select(Telemetry.received_at).order_by(Telemetry.received_at.desc()).limit(1)
    result = await db.execute(stmt)
    last_dt = result.scalar_one_or_none()
    
    age = None
    if last_dt:
        age = (datetime.now(timezone.utc) - last_dt).total_seconds()

    mqtt_client = getattr(request.app.state, "mqtt_client", None)
    mqtt_connected = mqtt_client.is_connected if mqtt_client else False
        
    return HealthResponse(
        mqtt_connected=mqtt_connected,
        db_ok=db_ok,
        last_telemetry_age_sec=age,
        device_id=settings.device_id
    )

@router.get("/api/analytics", response_model=AnalyticsResponse)
async def get_analytics(period: str = "24h", db: AsyncSession = Depends(get_db)):
    """Get aggregated analytics from database."""
    now = datetime.now(timezone.utc)
    delta = timedelta(hours=24) if period == "24h" else (timedelta(days=7) if period == "7d" else timedelta(hours=1))
    start_time = now - delta

    power_stmt = select(
        func.avg(Telemetry.power_w),
        func.max(Telemetry.power_w),
        func.count(Telemetry.id)
    ).where(Telemetry.received_at >= start_time)
    power_res = await db.execute(power_stmt)
    row = power_res.one_or_none()
    avg_power = float(row[0] or 0.0) if row else 0.0
    peak_power = float(row[1] or 0.0) if row else 0.0
    count = int(row[2] or 0) if row else 0

    total_energy_wh = round((avg_power * (count * 2.0 / 3600.0)), 2)

    error_stmt = select(func.avg(func.abs(SolarState.tracking_error_deg))).where(SolarState.calculated_at >= start_time)
    error_res = await db.execute(error_stmt)
    avg_error = float(error_res.scalar() or 0.0)

    fault_stmt = select(func.count(Event.id)).where(Event.received_at >= start_time)
    fault_res = await db.execute(fault_stmt)
    fault_count = int(fault_res.scalar() or 0)

    return AnalyticsResponse(
        period=period,
        total_energy_wh=total_energy_wh,
        avg_power_w=round(avg_power, 2),
        peak_power_w=round(peak_power, 2),
        avg_tracking_error_deg=round(avg_error, 2),
        fault_count=fault_count
    )

@router.post("/api/commands", response_model=CommandResponse)
async def send_command(
    req: CommandRequest,
    request: Request,
    api_key: str = Depends(verify_api_key),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db)
):
    """Send a command to a device."""
    cmd_id = f"CMD-{secrets.token_hex(3)[:5].upper()}"
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=5)
    
    cmd_record = Command(
        cmd_id=cmd_id,
        device_id=settings.device_id,
        created_at=now,
        expires_at=expires,
        command=req.command,
        params=req.params,
        status="pending"
    )
    db.add(cmd_record)
    await db.commit()

    mqtt_client = getattr(request.app.state, "mqtt_client", None)
    if mqtt_client and mqtt_client.is_connected:
        try:
            await mqtt_client.publish_command(settings.device_id, {
                "cmd_id": cmd_id,
                "command": req.command,
                "params": req.params or {}
            })
            cmd_record.status = "sent"
            await db.commit()
        except Exception as e:
            pass
    
    return CommandResponse(
        cmd_id=cmd_id,
        command=req.command,
        params=req.params,
        status=cmd_record.status,
        created_at=now,
        expires_at=expires
    )

@router.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for live updates."""
    await ws_manager.connect(websocket)
    try:
        while True:
            # Keep alive loop
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
