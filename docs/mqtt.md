# MQTT Specification

## Broker Overview
- **Broker**: Mosquitto
- **Network**: Local Wi-Fi only (Offline-first)
- **Device ID Convention**: `tracker-01`

## QoS & Topics

### Last Will
- **Topic**: `solar/tracker-01/status`
- **Payload**: `{"state": "offline"}`
- **QoS**: 1
- **Retained**: Yes

### Topics List

| Topic | QoS | Retained | Direction | Description |
| --- | --- | --- | --- | --- |
| `solar/tracker-01/telemetry` | 0 | No | ESP32 → Broker | Real-time sensor and power data |
| `solar/tracker-01/status` | 1 | Yes | ESP32 → Broker | Online/offline status and heartbeat |
| `solar/tracker-01/motor/state` | 1 | Yes | ESP32 → Broker | Current motor mode and position |
| `solar/tracker-01/cmd` | 1 | No | Broker → ESP32 | Commands sent to the device |
| `solar/tracker-01/cmd/ack` | 1 | No | ESP32 → Broker | Acknowledgment of received commands |
| `solar/tracker-01/alerts` | 1 | No | ESP32 → Broker | System alerts and fault notifications |

## Why Command IDs Matter
Command IDs (`cmd_id`) are critical to prevent replay attacks, handle network retries gracefully (idempotency), and allow the backend to match specific acknowledgments to the original request. 

## Time Handling
- **seq**: An incrementing integer per message type per boot to track message order and detect drops.
- **uptime_ms**: Milliseconds since the ESP32 last booted.
- **received_at**: Timestamp appended by the backend upon receipt.
- **Drop detection**: Backend checks for gaps in `seq` numbers.
- **Reboot detection**: Backend detects a drop in `uptime_ms` or `seq` resetting to 0.

## Example JSON Payloads

### Telemetry (`solar/tracker-01/telemetry`)
```json
{
  "schema_version": 1,
  "seq": 1045,
  "uptime_ms": 3600500,
  "voltage_v": 12.1,
  "current_a": 0.45,
  "power_w": 5.44,
  "panel_angle_deg": 45.0,
  "motor_steps": 200,
  "tept_top": 2048,
  "tept_bottom": 2050,
  "tept_left": 1024,
  "tept_right": 3072,
  "ina219_ok": true,
  "sensors_ok": true,
  "mode": "auto"
}
```

### Status (`solar/tracker-01/status`)
```json
{
  "schema_version": 1,
  "device_id": "tracker-01",
  "state": "online",
  "firmware_version": "1.0.0",
  "uptime_ms": 3600500,
  "ip_address": "192.168.1.100",
  "free_heap": 256000
}
```

### Motor State (`solar/tracker-01/motor/state`)
```json
{
  "schema_version": 1,
  "seq": 50,
  "uptime_ms": 3600500,
  "mode": "go_to_angle",
  "motor_enabled": true,
  "motor_moving": true,
  "panel_angle_deg": 42.5,
  "motor_steps": 190,
  "target_angle_deg": 45.0
}
```

### Command (`solar/tracker-01/cmd`)
```json
{
  "schema_version": 1,
  "cmd_id": "CMD-a1b2c",
  "created_at": "2026-10-03T12:00:00Z",
  "expires_at": "2026-10-03T12:05:00Z",
  "command": "go_to_angle",
  "params": {
    "angle_deg": 45.0
  }
}
```

### Command Ack (`solar/tracker-01/cmd/ack`)
```json
{
  "schema_version": 1,
  "cmd_id": "CMD-a1b2c",
  "seq": 20,
  "uptime_ms": 3601000,
  "status": "accepted"
}
```

### Alerts (`solar/tracker-01/alerts`)
```json
{
  "schema_version": 1,
  "seq": 5,
  "uptime_ms": 3605000,
  "alert_id": "ALT-001",
  "severity": "error",
  "code": "INA219_FAIL",
  "message": "Failed to read from INA219 sensor"
}
```
