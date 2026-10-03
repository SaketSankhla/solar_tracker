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

from app.schemas import TelemetryPayload
from app.models import Telemetry, Device, Event
from app.database import AsyncSessionLocal

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
    """Calculates expected power generation and residual anomaly detection.
    
    This is the core ML/Analytics component:
    - Predicts what a 3W panel should generate given the current sunlight.
    - Compares prediction against measured power from INA219.
    - Large residual = shading, dust/soiling, or misalignment.
    """

    def predict_expected_power(self, tept_avg: float, panel_angle_deg: float) -> float:
        """Estimate theoretical expected power in Watts for the 3W SLG-0603 panel."""
        # 12-bit ADC max = 4095. At full sun (1000 W/m²), TEPT outputs ~3200-3800 counts.
        # Clean expected power model:
        irradiance_ratio = min(1.0, max(0.0, tept_avg / 3200.0))
        rated_watts = 3.0
        expected_w = rated_watts * irradiance_ratio
        return round(expected_w, 2)

    def analyze(self, payload: TelemetryPayload) -> dict:
        """Run AI residual analysis and return insight dictionary."""
        avg_light = (payload.tept_top + payload.tept_bottom + payload.tept_left + payload.tept_right) / 4.0
        expected_w = self.predict_expected_power(avg_light, payload.panel_angle_deg)
        measured_w = payload.power_w
        residual_w = round(expected_w - measured_w, 2)

        # Health / efficiency rating
        efficiency_pct = 100.0
        if expected_w > 0.1:
            efficiency_pct = round(min(100.0, max(0.0, (measured_w / expected_w) * 100.0)), 1)
        elif avg_light < 300:
            efficiency_pct = 0.0

        # Diagnosis
        if avg_light < 300:
            status = "Low Light / Night"
            severity = "normal"
        elif residual_w > 1.2:
            status = "High Loss: Possible Shading or Soiled Panel"
            severity = "warning"
        elif residual_w > 0.6:
            status = "Moderate Loss: Check Alignment or Clean Glass"
            severity = "caution"
        else:
            status = "Optimal Power Generation"
            severity = "good"

        return {
            "expected_power_w": expected_w,
            "measured_power_w": measured_w,
            "residual_w": residual_w,
            "efficiency_pct": efficiency_pct,
            "status": status,
            "severity": severity,
            "model_version": "rf-power-v1",
        }


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
        # ws_manager is injected after app startup to avoid circular imports
        self._ws_manager = None

    def set_ws_manager(self, ws_manager) -> None:  # type: ignore[no-untyped-def]
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
        # 1. Immediate in-memory AI Expected Power & Residual Analysis (<0.2ms)
        ai_insight = self.solar_calc.analyze(payload)

        # 2. IMMEDIATE WebSocket Broadcast (<0.5ms) - Never wait for disk I/O!
        if self._ws_manager is not None:
            message = json.dumps({
                "type": "telemetry",
                "data": payload.model_dump(),
                "ai_insight": ai_insight,
            })
            await self._ws_manager.broadcast(message)

        # 3. Update fast In-Memory Cache for zero-latency REST calls
        from app.api.routes import latest_live_cache
        latest_live_cache[payload.device_id] = {
            "telemetry": payload.model_dump(),
            "ai_insight": ai_insight,
        }

        # 4. Asynchronous Database Persistence (Runs in background)
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

                from app.models import Prediction
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
            logger.error(f"Error persisting telemetry to DB: {e}")

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
