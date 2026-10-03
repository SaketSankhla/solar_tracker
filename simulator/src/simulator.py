"""
ESP32 Solar Tracker Simulator
==============================
Simulates the real ESP32 at the MQTT boundary for software pipeline testing.

⚠️  WARNING: This simulator generates synthetic data only.
    Data produced by this simulator MUST NEVER be used as real ML training data.

Usage:
    python src/simulator.py [options]

Options:
    --noise-level FLOAT    ADC noise amplitude in counts (default: 50)
    --cloud-factor FLOAT   0.0 = clear sky, 1.0 = fully overcast (default: 0.0)
    --drop-rate FLOAT      Fraction of telemetry messages to drop (default: 0.0)
    --ina219-fail          Simulate INA219 sensor failure
    --motor-stall          Simulate motor stall (angle doesn't change despite commands)
    --reboot-after INT     Seconds after which to simulate an ESP32 reboot (0 = never)
"""

import asyncio
import json
import logging
import random
import time
import argparse
import os
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import numpy as np
import pvlib
from aiomqtt import Client, MqttError, Will
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DEVICE_ID = os.getenv("DEVICE_ID", "tracker-01")
STEPS_PER_DEGREE = 1.0 / 1.8   # full-step: 200 steps = 360°, so 1.8°/step → ~0.556 steps/°


class SimulatorConfig:
    """Holds all configurable failure-mode flags."""
    def __init__(
        self,
        noise_level: float = 50.0,
        cloud_factor: float = 0.0,
        drop_rate: float = 0.0,
        ina219_fail: bool = False,
        motor_stall: bool = False,
        reboot_after: int = 0,
    ) -> None:
        self.noise_level = noise_level
        self.cloud_factor = cloud_factor
        self.drop_rate = drop_rate
        self.ina219_fail = ina219_fail
        self.motor_stall = motor_stall
        self.reboot_after = reboot_after


class ESP32Simulator:
    """Simulates the real ESP32 at the MQTT boundary.

    Publishes telemetry, motor state, and status just like the firmware.
    Receives commands and sends ACKs.
    """

    def __init__(self, config: SimulatorConfig) -> None:
        self.config = config
        self.broker = os.getenv("MQTT_HOST", "localhost")
        self.port = int(os.getenv("MQTT_PORT", "1883"))
        self.user = os.getenv("MQTT_USERNAME", "solartracker")
        self.password = os.getenv("MQTT_PASSWORD", "changeme")

        lat = float(os.getenv("LATITUDE", "51.5"))
        lon = float(os.getenv("LONGITUDE", "-0.1"))
        tz = os.getenv("TIMEZONE", "UTC")
        self.location = pvlib.location.Location(lat, lon, tz)

        # Simulated state
        self.state: str = "idle"           # auto | manual | go_to_angle | idle | fault
        self.panel_angle_deg: float = 0.0
        self.target_angle_deg: float = 0.0
        self.motor_steps: int = 0
        self.motor_enabled: bool = False
        self.motor_moving: bool = False

        # Sequence and timing
        self.seq: int = 0
        self.start_time: float = time.time()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def uptime_ms(self) -> int:
        return int((time.time() - self.start_time) * 1000)

    def _get_solar_position(self):
        """Return (elevation_deg, azimuth_deg) using pvlib."""
        times = pd.DatetimeIndex([datetime.now(timezone.utc)])
        sol = self.location.get_solarposition(times)
        return float(sol["apparent_elevation"].iloc[0]), float(sol["azimuth"].iloc[0])

    def _compute_tept(self, sun_elevation: float, sun_azimuth: float):
        """Simulate four TEPT ADC readings from solar geometry."""
        # Base irradiance (0–~1000 counts) from elevation
        base = 1000.0 * max(0.0, np.sin(np.radians(sun_elevation)))
        base *= (1.0 - self.config.cloud_factor * 0.8)

        noise = self.config.noise_level
        az_rad = np.radians(sun_azimuth)

        # Simple directional model: azimuth drives left/right split
        tept_top    = base + np.cos(az_rad) * 100 + random.uniform(-noise, noise)
        tept_bottom = base - np.cos(az_rad) * 100 + random.uniform(-noise, noise)
        tept_left   = base + np.sin(az_rad) * 100 + random.uniform(-noise, noise)
        tept_right  = base - np.sin(az_rad) * 100 + random.uniform(-noise, noise)

        # Clamp to 0–4095 (12-bit ADC)
        return (
            int(np.clip(tept_top,    0, 4095)),
            int(np.clip(tept_bottom, 0, 4095)),
            int(np.clip(tept_left,   0, 4095)),
            int(np.clip(tept_right,  0, 4095)),
        )

    def _compute_power(self, sun_elevation: float):
        """Simulate INA219 readings from solar irradiance."""
        if self.config.ina219_fail:
            return 0.0, 0.0, 0.0

        irr = max(0.0, np.sin(np.radians(sun_elevation)))
        irr *= (1.0 - self.config.cloud_factor * 0.8)

        rated_w = 3.0
        rated_v = 8.7

        power_w = rated_w * irr + random.uniform(-0.05, 0.05)
        power_w = max(0.0, power_w)
        voltage_v = rated_v * (0.8 + 0.2 * irr) if power_w > 0 else 0.0
        current_a = power_w / voltage_v if voltage_v > 0 else 0.0

        return round(voltage_v, 3), round(current_a, 3), round(power_w, 3)

    def _step_panel(self, tept_left: int, tept_right: int) -> None:
        """In AUTO mode: move panel toward brighter side if error > deadband."""
        diff = tept_left - tept_right
        deadband = 50
        step_deg = 1.8  # one full step

        if abs(diff) <= deadband:
            return

        direction = np.sign(diff)
        new_angle = self.panel_angle_deg + direction * step_deg
        new_angle = float(np.clip(new_angle, -90.0, 90.0))

        if not self.config.motor_stall:
            self.panel_angle_deg = new_angle
            self.motor_steps = int(self.panel_angle_deg * STEPS_PER_DEGREE)
            self.motor_moving = True
            self.motor_enabled = True
        # If motor_stall: angle stays the same

    def _move_toward_target(self) -> None:
        """In GO_TO_ANGLE mode: slew toward target."""
        diff = self.target_angle_deg - self.panel_angle_deg
        step = min(1.8, abs(diff))
        if abs(diff) <= 0.1:
            self.panel_angle_deg = self.target_angle_deg
            self.motor_moving = False
            if self.state == "go_to_angle":
                self.state = "idle"
        elif not self.config.motor_stall:
            self.panel_angle_deg += np.sign(diff) * step
            self.motor_steps = int(self.panel_angle_deg * STEPS_PER_DEGREE)
            self.motor_moving = True

    # ------------------------------------------------------------------
    # Async loops
    # ------------------------------------------------------------------

    async def _telemetry_loop(self, client: Client) -> None:
        topic = f"solar/{DEVICE_ID}/telemetry"
        while True:
            await asyncio.sleep(2.0)

            # Simulated reboot
            if self.config.reboot_after > 0:
                if (time.time() - self.start_time) > self.config.reboot_after:
                    logger.warning("=== Simulating ESP32 reboot ===")
                    self.start_time = time.time()
                    self.seq = 0

            elevation, azimuth = self._get_solar_position()
            t_top, t_bot, t_left, t_right = self._compute_tept(elevation, azimuth)
            v, i, p = self._compute_power(elevation)

            # Apply tracking logic
            if self.state == "auto":
                self._step_panel(t_left, t_right)
            elif self.state == "go_to_angle":
                self._move_toward_target()

            self.seq += 1

            # Random message drop
            if random.random() < self.config.drop_rate:
                logger.info("Dropped telemetry seq=%d", self.seq)
                continue

            payload = {
                "schema_version": 1,
                "seq": self.seq,
                "uptime_ms": self.uptime_ms,
                "voltage_v": v,
                "current_a": i,
                "power_w": p,
                "panel_angle_deg": round(self.panel_angle_deg, 2),
                "motor_steps": self.motor_steps,
                "tept_top": t_top,
                "tept_bottom": t_bot,
                "tept_left": t_left,
                "tept_right": t_right,
                "ina219_ok": not self.config.ina219_fail,
                "sensors_ok": True,
                "mode": self.state,
            }

            try:
                await client.publish(topic, json.dumps(payload), qos=0)
                logger.debug("Telemetry seq=%d mode=%s angle=%.1f°", self.seq, self.state, self.panel_angle_deg)
            except Exception as exc:
                logger.error("Failed to publish telemetry: %s", exc)

    async def _motor_state_loop(self, client: Client) -> None:
        topic = f"solar/{DEVICE_ID}/motor/state"
        seq_ms = self.seq
        while True:
            await asyncio.sleep(0.5)
            payload = {
                "schema_version": 1,
                "seq": self.seq,
                "uptime_ms": self.uptime_ms,
                "mode": self.state,
                "motor_enabled": self.motor_enabled,
                "motor_moving": self.motor_moving,
                "panel_angle_deg": round(self.panel_angle_deg, 2),
                "motor_steps": self.motor_steps,
            }
            if self.state == "go_to_angle":
                payload["target_angle_deg"] = round(self.target_angle_deg, 2)
            try:
                await client.publish(topic, json.dumps(payload), qos=1, retain=True)
            except Exception:
                pass

    async def _command_loop(self, client: Client) -> None:
        """Receive and dispatch commands, publish ACKs."""
        cmd_topic = f"solar/{DEVICE_ID}/cmd"
        ack_topic = f"solar/{DEVICE_ID}/cmd/ack"
        await client.subscribe(cmd_topic, qos=1)
        logger.info("Subscribed to %s", cmd_topic)

        async for message in client.messages:
            if str(message.topic) != cmd_topic:
                continue
            try:
                data = json.loads(message.payload.decode())
                cmd_id  = data.get("cmd_id", "UNKNOWN")
                command = data.get("command", "")
                params  = data.get("params", {})

                logger.info("Command received: cmd_id=%s command=%s params=%s", cmd_id, command, params)

                status = "accepted"
                reason: Optional[str] = None

                if command == "set_mode":
                    mode = params.get("mode", "idle").lower()
                    if mode in ("auto", "manual", "idle"):
                        self.state = mode
                        self.motor_enabled = mode != "idle"
                    else:
                        status = "rejected"
                        reason = f"Unknown mode: {mode}"

                elif command == "go_to_angle":
                    angle = float(params.get("angle_deg", 0.0))
                    if -90.0 <= angle <= 90.0:
                        self.state = "go_to_angle"
                        self.target_angle_deg = angle
                        self.motor_enabled = True
                    else:
                        status = "rejected"
                        reason = "angle_deg out of [-90, 90]"

                elif command == "stop":
                    self.state = "idle"
                    self.target_angle_deg = self.panel_angle_deg
                    self.motor_moving = False
                    self.motor_enabled = False

                elif command == "home":
                    # Software home: slew to 0°
                    self.state = "go_to_angle"
                    self.target_angle_deg = 0.0
                    self.motor_enabled = True

                else:
                    status = "rejected"
                    reason = f"Unknown command: {command}"

                ack = {
                    "schema_version": 1,
                    "cmd_id": cmd_id,
                    "seq": self.seq,
                    "uptime_ms": self.uptime_ms,
                    "status": status,
                }
                if reason:
                    ack["reason"] = reason

                await client.publish(ack_topic, json.dumps(ack), qos=1)

            except Exception as exc:
                logger.error("Error processing command: %s", exc)

    async def run(self) -> None:
        """Connect to MQTT broker and run all loops concurrently."""
        logger.info("═══════════════════════════════════════════════")
        logger.info("Solar Tracker Simulator — device: %s", DEVICE_ID)
        logger.info("Broker: %s:%d", self.broker, self.port)
        logger.info("Location: lat=%.4f lon=%.4f", self.location.latitude, self.location.longitude)
        logger.info(
            "Failures: noise=%.0f cloud=%.1f drop=%.2f ina219_fail=%s stall=%s reboot_after=%ds",
            self.config.noise_level, self.config.cloud_factor, self.config.drop_rate,
            self.config.ina219_fail, self.config.motor_stall, self.config.reboot_after,
        )
        logger.info("═══════════════════════════════════════════════")

        status_topic = f"solar/{DEVICE_ID}/status"
        online_payload = json.dumps({
            "schema_version": 1,
            "device_id": DEVICE_ID,
            "state": "online",
            "firmware_version": "sim-0.1.0",
            "uptime_ms": 0,
        })
        offline_payload = json.dumps({
            "schema_version": 1,
            "device_id": DEVICE_ID,
            "state": "offline",
            "firmware_version": "sim-0.1.0",
            "uptime_ms": 0,
        })

        last_will = Will(
            topic=status_topic,
            payload=offline_payload.encode(),
            qos=1,
            retain=True,
        )

        while True:
            try:
                async with Client(
                    hostname=self.broker,
                    port=self.port,
                    username=self.user,
                    password=self.password,
                    will=last_will,
                ) as client:
                    # Announce online
                    await client.publish(status_topic, online_payload, qos=1, retain=True)
                    logger.info("Connected to broker — status: online")

                    await asyncio.gather(
                        self._telemetry_loop(client),
                        self._motor_state_loop(client),
                        self._command_loop(client),
                    )
            except MqttError as exc:
                logger.error("MQTT error: %s — reconnecting in 5s", exc)
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                logger.info("Simulator stopped")
                break
            except Exception:
                logger.exception("Unexpected error — reconnecting in 5s")
                await asyncio.sleep(5)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="ESP32 Solar Tracker Simulator",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--noise-level",  type=float, default=50.0,  help="ADC noise amplitude (counts)")
    parser.add_argument("--cloud-factor", type=float, default=0.0,   help="Cloud factor 0.0–1.0")
    parser.add_argument("--drop-rate",    type=float, default=0.0,   help="Telemetry drop fraction 0.0–1.0")
    parser.add_argument("--ina219-fail",  action="store_true",        help="Simulate INA219 failure")
    parser.add_argument("--motor-stall",  action="store_true",        help="Simulate motor stall")
    parser.add_argument("--reboot-after", type=int,   default=0,     help="Seconds until simulated reboot (0=never)")
    args = parser.parse_args()

    config = SimulatorConfig(
        noise_level=args.noise_level,
        cloud_factor=args.cloud_factor,
        drop_rate=args.drop_rate,
        ina219_fail=args.ina219_fail,
        motor_stall=args.motor_stall,
        reboot_after=args.reboot_after,
    )

    sim = ESP32Simulator(config)
    try:
        asyncio.run(sim.run())
    except KeyboardInterrupt:
        logger.info("Simulator stopped by user.")


