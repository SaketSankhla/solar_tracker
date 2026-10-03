"""Contract tests — verify JSON schemas against example payloads.

Tests ensure:
  1. Valid payloads pass schema validation.
  2. Removing any required field causes a ValidationError.
  3. Adding extra fields causes a ValidationError (additionalProperties: false).

The schemas live in docs/schemas/ and represent the contract between
the ESP32 firmware, the simulator, the backend, and the dashboard.
"""

import json
from pathlib import Path
from typing import Any, Dict
import jsonschema
import pytest


def load_schema(schemas_dir: Path, name: str) -> Dict[str, Any]:
    """Load a JSON schema from docs/schemas/<name>.schema.json."""
    path = schemas_dir / f"{name}.schema.json"
    assert path.is_file(), f"Schema not found: {path}"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Parametrised valid-payload tests
# ---------------------------------------------------------------------------

SCHEMA_FIXTURE_PAIRS = [
    ("telemetry",    "valid_telemetry"),
    ("status",       "valid_status"),
    ("motor-state",  "valid_motor_state"),
    ("command",      "valid_command"),
    ("command-ack",  "valid_command_ack"),
    ("alert",        "valid_alert"),
]


@pytest.mark.parametrize("schema_name,fixture_name", SCHEMA_FIXTURE_PAIRS)
def test_valid_payload_passes(
    schemas_dir: Path,
    schema_name: str,
    fixture_name: str,
    request: pytest.FixtureRequest,
) -> None:
    """A valid payload must pass JSON Schema validation."""
    schema = load_schema(schemas_dir, schema_name)
    payload = request.getfixturevalue(fixture_name)
    jsonschema.validate(instance=payload, schema=schema)  # raises on failure


@pytest.mark.parametrize("schema_name,fixture_name", SCHEMA_FIXTURE_PAIRS)
def test_missing_required_field_fails(
    schemas_dir: Path,
    schema_name: str,
    fixture_name: str,
    request: pytest.FixtureRequest,
) -> None:
    """Removing any required field must raise ValidationError."""
    schema = load_schema(schemas_dir, schema_name)
    payload: Dict[str, Any] = dict(request.getfixturevalue(fixture_name))
    required = schema.get("required", [])

    assert required, f"Schema '{schema_name}' has no required fields — check schema definition"

    for field in required:
        trimmed = {k: v for k, v in payload.items() if k != field}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=trimmed, schema=schema)


@pytest.mark.parametrize("schema_name,fixture_name", SCHEMA_FIXTURE_PAIRS)
def test_extra_fields_rejected(
    schemas_dir: Path,
    schema_name: str,
    fixture_name: str,
    request: pytest.FixtureRequest,
) -> None:
    """Extra fields must be rejected (additionalProperties: false)."""
    schema = load_schema(schemas_dir, schema_name)
    assert schema.get("additionalProperties") is False, (
        f"Schema '{schema_name}' must set additionalProperties: false"
    )
    payload = dict(request.getfixturevalue(fixture_name))
    payload["__extra__"] = "should_not_be_here"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=payload, schema=schema)


# ---------------------------------------------------------------------------
# Individual smoke tests (explicit for readability in test output)
# ---------------------------------------------------------------------------

def test_telemetry_schema_smoke(schemas_dir: Path, valid_telemetry: Dict[str, Any]) -> None:
    jsonschema.validate(instance=valid_telemetry, schema=load_schema(schemas_dir, "telemetry"))


def test_status_schema_smoke(schemas_dir: Path, valid_status: Dict[str, Any]) -> None:
    jsonschema.validate(instance=valid_status, schema=load_schema(schemas_dir, "status"))


def test_motor_state_schema_smoke(schemas_dir: Path, valid_motor_state: Dict[str, Any]) -> None:
    jsonschema.validate(instance=valid_motor_state, schema=load_schema(schemas_dir, "motor-state"))


def test_command_schema_smoke(schemas_dir: Path, valid_command: Dict[str, Any]) -> None:
    jsonschema.validate(instance=valid_command, schema=load_schema(schemas_dir, "command"))


def test_command_ack_schema_smoke(schemas_dir: Path, valid_command_ack: Dict[str, Any]) -> None:
    jsonschema.validate(instance=valid_command_ack, schema=load_schema(schemas_dir, "command-ack"))


def test_alert_schema_smoke(schemas_dir: Path, valid_alert: Dict[str, Any]) -> None:
    jsonschema.validate(instance=valid_alert, schema=load_schema(schemas_dir, "alert"))
