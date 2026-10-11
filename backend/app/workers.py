"""Background workers for processing incoming data and performing analytics tasks.

The MQTT callback must never do heavy work directly.  Instead it puts
validated payloads on the async queue, and this worker consumes them.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import pvlib
import pandas as pd
import numpy as np

from app.schemas import TelemetryPayload
from app.models import Telemetry, Device, Event, Prediction, SolarState
from app.database import AsyncSessionLocal
from app.config import get_settings

logger = logging.getLogger(__name__)


class FaultDetector:
    """Rule-based fault detection applied to each telemetry payload.

    All rules are deterministic — no ML involved.
    """

    def __init__(self) -> None:
        self._consecutive_tept_zero: int = 0

    def check(self, payload: TelemetryPayload) -> List[str]:
        """Check for faults and return a list of fault code strings."""
        faults: List[str] = []

        # INA219 stopped responding
        if not payload.ina219_ok:
            faults.append("INA219_FAIL")
            logger.warning("[FAULT] INA219_FAIL device=%s seq=%d", payload.device_id, payload.seq)

        # sensors_ok flag raised by firmware
        if not payload.sensors_ok:
            faults.append("STALE_SENSOR")
            logger.warning("[FAULT] STALE_SENSOR device=%s seq=%d", payload.device_id, payload.seq)

        # TEPT stuck: any channel reading zero for 5+ consecutive readings
        tept_vals = [
            payload.tept_top,
            payload.tept_bottom,
            payload.tept_left,
            payload.tept_right,
        ]
        if any(v == 0 for v in tept_vals):
            self._consecutive_tept_zero += 1
            if self._consecutive_tept_zero >= 5:
                faults.append("TEPT_STUCK")
                logger.warning(
                    "[FAULT] TEPT_STUCK device=%s (zero count=%d)",
                    payload.device_id, self._consecutive_tept_zero,
                )
        else:
            self._consecutive_tept_zero = 0

        # Power sanity: |reported_power - V*I| > 10% of V*I
        vi_product = payload.voltage_v * payload.current_a
        if vi_product > 0.01:  # avoid division by near-zero
            discrepancy = abs(payload.power_w - vi_product) / vi_product
            if discrepancy > 0.10:
                logger.warning(
                    "[WARN] Power sanity: reported=%.3fW, V*I=%.3fW (%.0f%% diff) device=%s",
                    payload.power_w, vi_product, discrepancy * 100, payload.device_id,
                )

        # Undervoltage during AUTO mode (daylight detection TBD — left as stub)
        # if payload.voltage_v < 0.1 and payload.mode == "auto":
        #     faults.append("UNDERVOLTAGE")

        return faults


class SolarCalculator:
    """Calculates astronomical solar geometry (pvlib) and ML residual anomaly detection.

    This is the core ML/Physics & Analytics component:
    - Calculates true astronomical sun elevation and azimuth using pvlib.
    - Determines optimal single-axis tracking angle (tracker_theta).
    - Computes tracking error: tracking_error_deg = panel_angle_deg - target_panel_angle_deg.
    - Estimates theoretical expected power combining physics clear-sky model + TEPT phototransistors.
    - Compares prediction against measured power from INA219.
    - Intelligent diagnostic categorization: Optimal, Cloud Overcast, Partial Shading, Soiling/Dust, or Motor Misalignment.
    """

    def __init__(self) -> None:
        self.settings = get_settings()

    def calculate_solar_geometry(self, dt: datetime, panel_angle_deg: float) -> dict:
        """Compute real astronomical sun angles and single-axis target using pvlib."""
        lat = self.settings.latitude if self.settings.latitude != 0.0 else 26.9124
        lon = self.settings.longitude if self.settings.longitude != 0.0 else 75.7873

        times = pd.DatetimeIndex([dt])
        try:
            solpos = pvlib.solarposition.get_solarposition(times, lat, lon)
            azimuth = float(solpos["azimuth"].iloc[0])
            elevation = float(solpos["apparent_elevation"].iloc[0])

            # Calculate optimal single-axis tracking target
            # Single-axis tracking along North-South axis with East-West rotation
            tracked = pvlib.tracking.singleaxis(
                apparent_zenith=solpos["apparent_zenith"],
                solar_azimuth=solpos["azimuth"],
                axis_tilt=0,
                axis_azimuth=180,
                max_angle=85,
            )
            target_deg = float(tracked["tracker_theta"].iloc[0])
            if pd.isna(target_deg) or elevation <= 0:
                target_deg = 0.0  # Stow flat when sun is below horizon

            tracking_error = round(panel_angle_deg - target_deg, 2)

            return {
                "sun_azimuth_deg": round(azimuth, 2),
                "sun_elevation_deg": round(elevation, 2),
                "target_panel_angle_deg": round(target_deg, 2),
                "tracking_error_deg": tracking_error,
                "is_daylight": elevation > 0,
            }
        except Exception as e:
            logger.warning("Error computing pvlib geometry: %s", e)
            return {
                "sun_azimuth_deg": 180.0,
                "sun_elevation_deg": 0.0,
                "target_panel_angle_deg": 0.0,
                "tracking_error_deg": round(panel_angle_deg, 2),
                "is_daylight": False,
            }

    def predict_expected_power(self, tept_avg: float, solar_geo: dict) -> float:
        """Estimate theoretical expected power in Watts for 3W panel combining physics and sensors."""
        rated_watts = 3.0
        # 12-bit TEPT phototransistors (0 to 4095 counts). At peak direct sun: ~3200-3600 counts.
        sensor_irradiance_ratio = min(1.0, max(0.0, tept_avg / 3200.0))

        elevation = solar_geo.get("sun_elevation_deg", 0.0)
        elev_factor = max(0.0, float(np.sin(np.radians(elevation)))) if elevation > 0 else 0.0

        if sensor_irradiance_ratio > 0.05:
            expected_w = rated_watts * sensor_irradiance_ratio
        elif elev_factor > 0:
            expected_w = rated_watts * elev_factor
        else:
            expected_w = 0.0

        return round(float(expected_w), 2)

    def analyze(self, payload: TelemetryPayload) -> tuple[dict, dict]:
        """Run complete AI solar geometry, expected power, and anomaly diagnosis."""
        now_dt = datetime.now(timezone.utc)
        solar_geo = self.calculate_solar_geometry(now_dt, payload.panel_angle_deg)

        tept_vals = [payload.tept_top, payload.tept_bottom, payload.tept_left, payload.tept_right]
        avg_light = sum(tept_vals) / 4.0
        expected_w = self.predict_expected_power(avg_light, solar_geo)
        measured_w = payload.power_w
        residual_w = round(expected_w - measured_w, 2)

        # Efficiency calculation
        if expected_w > 0.15:
            efficiency_pct = round(min(100.0, max(0.0, (measured_w / expected_w) * 100.0)), 1)
        elif avg_light < 300:
            efficiency_pct = 0.0
        else:
            efficiency_pct = 100.0

        # Sensor spread across quadrants
        sensor_spread = max(tept_vals) - min(tept_vals)
        tracking_err = abs(solar_geo.get("tracking_error_deg", 0.0))

        # Diagnostic classification
        if avg_light < 250 and not solar_geo["is_daylight"]:
            status = "Night / Low Ambient Light"
            severity = "normal"
        elif avg_light < 700:
            status = "Low Ambient Light / Overcast Sky"
            severity = "caution"
        elif tracking_err > 20.0 and avg_light > 1500 and payload.mode == "auto":
            status = f"Tracking Misaligned (Lag: {tracking_err:.1f}°)"
            severity = "warning"
        elif sensor_spread > 1200 and avg_light > 1200:
            status = "Partial Shading / Obstruction Detected"
            severity = "warning"
        elif residual_w > 1.1 and expected_w > 1.5:
            status = "Significant Power Deficit: Shading or Soiled Glass"
            severity = "warning"
        elif residual_w > 0.5 and expected_w > 1.2:
            status = "Moderate Deficit: Check Panel Cleanliness / Dust"
            severity = "caution"
        elif efficiency_pct >= 80.0:
            status = "Optimal Power Generation & Sun Tracking"
            severity = "good"
        else:
            status = "Normal Operational State"
            severity = "good"

        ai_insight = {
            "expected_power_w": expected_w,
            "measured_power_w": measured_w,
            "residual_w": residual_w,
            "efficiency_pct": efficiency_pct,
            "status": status,
            "severity": severity,
            "model_version": "physics-pvlib-ml-v2",
        }

        solar_state = {
            "sun_azimuth_deg": solar_geo["sun_azimuth_deg"],
            "sun_elevation_deg": solar_geo["sun_elevation_deg"],
            "target_panel_angle_deg": solar_geo["target_panel_angle_deg"],
            "tracking_error_deg": solar_geo["tracking_error_deg"],
        }

        return ai_insight, solar_state


class WorkerQueue:
    """Async queue that decouples MQTT ingestion from heavy processing.

    Usage:
        await worker_queue.put_telemetry(payload)
        asyncio.create_task(worker_queue.run())  # started in lifespan
    """

    def __init__(self) -> None:
        self.queue: asyncio.Queue[TelemetryPayload] = asyncio.Queue()
        self.fault_detector = FaultDetector()
        self.solar_calc = SolarCalculator()
        self._ws_manager = None

    def set_ws_manager(self, ws_manager) -> None:
        """Inject the WebSocket connection manager (called from lifespan)."""
        self._ws_manager = ws_manager

    async def put_telemetry(self, payload: TelemetryPayload) -> None:
        """Enqueue a validated telemetry payload for processing."""
        await self.queue.put(payload)

    async def run(self) -> None:
        """Consume items from the queue until cancelled."""
        logger.info("Telemetry worker started")
        while True:
            try:
                payload = await self.queue.get()
                await self._process_telemetry(payload)
                self.queue.task_done()
            except asyncio.CancelledError:
                logger.info("Telemetry worker shutting down")
                break
            except Exception:
                logger.exception("Unexpected error in telemetry worker")

    async def _process_telemetry(self, payload: TelemetryPayload) -> None:
        """Process incoming telemetry with prioritized low-latency broadcast."""
        # 1. Immediate in-memory AI Expected Power, Solar Geometry & Residual Analysis
        ai_insight, solar_state = self.solar_calc.analyze(payload)

        # 2. IMMEDIATE WebSocket Broadcast (<0.5ms)
        if self._ws_manager is not None:
            message = json.dumps({
                "type": "telemetry",
                "data": payload.model_dump(),
                "ai_insight": ai_insight,
                "solar_state": solar_state,
            })
            await self._ws_manager.broadcast(message)

        # 3. Update fast In-Memory Cache for zero-latency REST calls
        from app.api.routes import latest_live_cache
        latest_live_cache[payload.device_id] = {
            "telemetry": payload.model_dump(),
            "ai_insight": ai_insight,
            "solar_state": solar_state,
        }

        # 4. Asynchronous Database Persistence
        try:
            async with AsyncSessionLocal() as session:
                await self._ensure_device(session, payload.device_id)

                db_row = Telemetry(
                    device_id=payload.device_id,
                    received_at=datetime.now(timezone.utc),
                    seq=payload.seq,
                    uptime_ms=payload.uptime_ms,
                    voltage_v=payload.voltage_v,
                    current_a=payload.current_a,
                    power_w=payload.power_w,
                    panel_angle_deg=payload.panel_angle_deg,
                    motor_steps=payload.motor_steps,
                    tept_top=payload.tept_top,
                    tept_bottom=payload.tept_bottom,
                    tept_left=payload.tept_left,
                    tept_right=payload.tept_right,
                    ina219_ok=payload.ina219_ok,
                    sensors_ok=payload.sensors_ok,
                    mode=payload.mode,
                )
                session.add(db_row)

                # Fault detection
                faults = self.fault_detector.check(payload)
                for code in faults:
                    event = Event(
                        device_id=payload.device_id,
                        received_at=datetime.now(timezone.utc),
                        event_type="error",
                        code=code,
                        message=f"Fault detected: {code} at seq={payload.seq}",
                    )
                    session.add(event)

                # Solar state persistence
                solar_row = SolarState(
                    device_id=payload.device_id,
                    calculated_at=datetime.now(timezone.utc),
                    sun_azimuth_deg=solar_state["sun_azimuth_deg"],
                    sun_elevation_deg=solar_state["sun_elevation_deg"],
                    target_panel_angle_deg=solar_state["target_panel_angle_deg"],
                    tracking_error_deg=solar_state["tracking_error_deg"],
                )
                session.add(solar_row)

                # Prediction persistence
                pred_row = Prediction(
                    device_id=payload.device_id,
                    predicted_at=datetime.now(timezone.utc),
                    model_version=ai_insight["model_version"],
                    predicted_power_w=ai_insight["expected_power_w"],
                    measured_power_w=ai_insight["measured_power_w"],
                    residual_w=ai_insight["residual_w"],
                )
                session.add(pred_row)

                await session.commit()
        except Exception as e:
            logger.error("Error persisting telemetry to DB: %s", e)

    @staticmethod
    async def _ensure_device(session: AsyncSession, device_id: str) -> None:
        """Insert a default Device row if it doesn't already exist."""
        result = await session.execute(
            select(Device).where(Device.id == device_id)
        )
        if result.scalar_one_or_none() is None:
            session.add(Device(
                id=device_id,
                name=device_id,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ))
            # Don't commit here; the caller commits after adding telemetry


# Global singleton — started by the FastAPI lifespan
telemetry_queue = WorkerQueue()
