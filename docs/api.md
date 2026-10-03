# API Specification

## Base Configuration
- **Base URL**: `http://127.0.0.1:8000`
- **Authentication**: `X-API-Key` header required for all `POST` endpoints.
- **CORS Policy**: Configured to allow origins from the frontend dashboard only (e.g., `http://localhost:5173`).

## REST Endpoints

### `GET /api/live`
Returns the latest telemetry and calculated solar state for a given device.

### `GET /api/history`
Query parameters:
- `device_id` (string)
- `start` (timestamp)
- `end` (timestamp)
- `limit` (integer, default 100)
Returns paginated historical telemetry data.

### `GET /api/health`
Returns the overall service health:
- MQTT broker connection status
- Database connection status
- Age of the last received telemetry (to detect stale states)

### `GET /api/analytics`
Query parameters:
- `device_id` (string)
- `period` (string, e.g., 'day', 'week', 'month')
Returns aggregated analytics including daily energy generation, tracking accuracy, and fault counts.

### `POST /api/commands`
Sends a command to the specified device via MQTT.

**Request:**
```json
{
  "device_id": "tracker-01",
  "command": "go_to_angle",
  "params": {
    "angle_deg": 30.0
  }
}
```

**Response:**
```json
{
  "status": "success",
  "cmd_id": "CMD-1a2b3",
  "message": "Command published to MQTT"
}
```
*Note: The backend generates the `cmd_id` and publishes the full JSON matching `command.schema.json` to the MQTT broker.*

#### Supported Commands
- `set_mode`: Sets the operational mode (e.g., `{"mode": "auto"}`)
- `go_to_angle`: Moves to a specific angle (e.g., `{"angle_deg": 45.0}`)
- `stop`: Immediately stops the motor
- `home`: Triggers the homing sequence

### Error Response Format
```json
{
  "error": "Unauthorized",
  "message": "Invalid API key provided"
}
```

## WebSocket Stream

### `WS /ws/live`
Provides a live, read-only stream of events from the system.

**Message Types:**
- `telemetry`: Broadcasts the latest raw telemetry payloads as they arrive.
- `solar_state`: Broadcasts updated `pvlib` sun position calculations.
- `alert`: Broadcasts system faults or warnings.
- `command_ack`: Broadcasts the result of sent commands.
