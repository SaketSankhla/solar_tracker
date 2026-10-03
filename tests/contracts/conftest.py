"""Pytest fixtures for JSON schema contract tests.

These fixtures provide example payloads that match the JSON schemas
defined in docs/schemas/.  They are the single source of truth for
what a valid message from the ESP32 (or simulator) looks like.
"""

from pathlib import Path
from typing import Any, Dict
import pytest


@pytest.fixture(scope="session")
def schemas_dir() -> Path:
    """Return the absolute path to docs/schemas/."""
    # Walk up from tests/contracts/ → tests/ → project root
    project_root = Path(__file__).resolve().parents[2]
    return project_root / "docs" / "schemas"


# ---------------------------------------------------------------------------
# Valid payload fixtures — must match every required field in the schemas
# ---------------------------------------------------------------------------

@pytest.fixture
def valid_telemetry() -> Dict[str, Any]:
    """Complete, valid telemetry payload (matches telemetry.schema.json)."""
    return {
        "schema_version": 1,
        "seq": 1042,
        "uptime_ms": 183920,
        "voltage_v": 8.12,
        "current_a": 0.31,
        "power_w": 2.52,
        "panel_angle_deg": 71.8,
        "motor_steps": 399,
        "tept_top": 2740,
        "tept_bottom": 2310,
        "tept_left": 2630,
        "tept_right": 2590,
        "ina219_ok": True,
        "sensors_ok": True,
        "mode": "auto",
    }


@pytest.fixture
def valid_status() -> Dict[str, Any]:
    """Complete, valid status payload (matches status.schema.json)."""
    return {
        "schema_version": 1,
        "device_id": "tracker-01",
        "state": "online",
        "firmware_version": "0.1.0",
        "uptime_ms": 183920,
    }


@pytest.fixture
def valid_motor_state() -> Dict[str, Any]:
    """Complete, valid motor-state payload (matches motor-state.schema.json)."""
    return {
        "schema_version": 1,
        "seq": 1042,
        "uptime_ms": 183920,
        "mode": "auto",
        "motor_enabled": True,
        "motor_moving": False,
        "panel_angle_deg": 71.8,
        "motor_steps": 399,
    }


@pytest.fixture
def valid_command() -> Dict[str, Any]:
    """Complete, valid command payload (matches command.schema.json)."""
    return {
        "schema_version": 1,
        "cmd_id": "CMD-83f21",
        "created_at": "2026-10-03T12:00:00Z",
        "expires_at": "2026-10-03T12:05:00Z",
        "command": "set_mode",
        "params": {"mode": "auto"},
    }


@pytest.fixture
def valid_command_ack() -> Dict[str, Any]:
    """Complete, valid command-ack payload (matches command-ack.schema.json)."""
    return {
        "schema_version": 1,
        "cmd_id": "CMD-83f21",
        "seq": 1043,
        "uptime_ms": 184020,
        "status": "accepted",
    }


@pytest.fixture
def valid_alert() -> Dict[str, Any]:
    """Complete, valid alert payload (matches alert.schema.json)."""
    return {
        "schema_version": 1,
        "seq": 10,
        "uptime_ms": 184500,
        "alert_id": "ALT-001",
        "severity": "warning",
        "code": "INA219_FAIL",
        "message": "INA219 stopped responding on I2C bus",
    }
