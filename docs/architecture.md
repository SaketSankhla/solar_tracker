# System Architecture

## Table of Contents

1. [System Overview](#1-system-overview)
2. [ESP32 Firmware](#2-esp32-firmware)
3. [Operating Modes](#3-operating-modes)
4. [Motor Safety](#4-motor-safety)
5. [Angle Tracking](#5-angle-tracking)
6. [Backend](#6-backend)
7. [Database](#7-database)
8. [Data Retention](#8-data-retention)
9. [Solar Calculations](#9-solar-calculations)
10. [AI / ML Component](#10-ai--ml-component)
11. [Rule-Based Fault Detection](#11-rule-based-fault-detection)
12. [Frontend](#12-frontend)
13. [REST + WebSocket Communication](#13-rest--websocket-communication)
14. [Security Model](#14-security-model)
15. [Offline-First Philosophy](#15-offline-first-philosophy)

---

## 1. System Overview

```
                   SUN
                    ↓
        ┌──────────────────────┐
        │   Solar Panel        │
        │      3 W             │
        └──────────┬───────────┘
                   │
              Mechanical
                rotation
                   │
              NEMA 17
                   │
                A4988
                   │
                 ESP32
              ┌────┴─────┐
              │          │
        TEPT4400 ×4    INA219
              │          │
              └────┬─────┘
                   │
                Wi-Fi
                   │
                MQTT
                   │
             ┌─────▼─────┐
             │ Mosquitto │
             └─────┬─────┘
                   │
             Python Backend
                   │
        ┌──────────┼──────────┐
        │          │          │
      SQLite     pvlib       ML
        │          │          │
        └──────────┼──────────┘
                   │
              FastAPI
                   │
              WebSocket
                   │
             React Dashboard
```

**Key principle:** The ESP32 tracks the sun independently. The backend is for monitoring, analytics, configuration, and visualisation. If the backend goes offline, the physical tracker continues operating.

---

## 2. ESP32 Firmware

**Language:** C++ (Arduino framework)  
**Build system:** PlatformIO

### Libraries

| Library | Purpose |
|---|---|
| ArduinoJson | JSON serialisation / deserialisation |
| AccelStepper | Stepper motor control with acceleration |
| Adafruit INA219 | Power monitor I²C driver |
| PubSubClient | MQTT client |

### Firmware loop

```
read sensors (TEPT × 4 oversampled, INA219)
         ↓
filter / average readings
         ↓
calculate sunlight error
  error_horizontal = left − right
  error_vertical   = top  − bottom
         ↓
tracking control (state-machine dispatch)
         ↓
move motor (AccelStepper)
         ↓
update dead-reckoned angle
         ↓
publish telemetry (every 2 s)
publish motor state (every 500 ms, retained)
         ↓
handle MQTT reconnect if needed
```

### ADC pin assignment

ADC1 pins only — ADC2 conflicts with Wi-Fi when active.

| Sensor | GPIO |
|---|---|
| TEPT Top | 36 |
| TEPT Bottom | 39 |
| TEPT Left | 34 |
| TEPT Right | 35 |

ADC readings are oversampled 16× and averaged to reduce noise.

---

## 3. Operating Modes

The firmware has a single mode/state machine. Only one mode is active at a time — the backend and dashboard cannot create conflicting parallel controllers.

### AUTO
The ESP32 continuously reads the TEPT phototransistors and adjusts the panel angle:
- Compute `error_horizontal = tept_left − tept_right`
- If `|error| > TEPT_DEADBAND`, step the motor toward the brighter side
- Operates entirely without backend involvement

### MANUAL
Motor movement is suspended. The backend/dashboard may request discrete movements. The ESP32 validates and executes them.

### GO_TO_ANGLE
The ESP32 moves the panel to a requested target angle using AccelStepper. The backend sends the target; the ESP32 is responsible for safe execution.

### IDLE
Motor disabled (A4988 ENABLE line deasserted). No tracking. Panel stays at current position.

### FAULT
Motor disabled. No movement until the fault is explicitly cleared. The fault reason is published to `solar/tracker-01/alerts`.

---

## 4. Motor Safety

The **ESP32 — not the backend** — is responsible for all motor safety. The backend may request movements but the firmware enforces all limits.

Safety mechanisms implemented in firmware:

- **Software angle limits** — clamp to `[−90°, +90°]`; refuse movement beyond bounds
- **Command expiration** — commands with `expires_at` in the past are rejected with `expired` ACK
- **Command validation** — malformed commands are rejected with `rejected` ACK
- **Movement rate limiting** — AccelStepper max speed and acceleration are capped
- **Fault state** — on fault, motor is disabled; no movement until cleared
- **Motor enable/disable** — A4988 ENABLE line managed explicitly
- **Duplicate command handling** — duplicate `cmd_id` values are rejected with `duplicate` ACK
- **Single active control mode** — only one mode active; mode transitions are explicit

> **Limit switch note:** The current BOM does not include a limit switch. True physical homing cannot be guaranteed without a reference mechanism. The software architecture supports homing/limit switches; the `home` command is a stub until a limit switch is added.

---

## 5. Angle Tracking

Panel angle is **dead-reckoned from motor steps**. There is no absolute encoder.

```
initial_angle = 0°

after N motor steps:
  angle_deg = N × 1.8°   (full-step, 1.8°/step)
```

The actual mechanical relationship will be calibrated during hardware integration because the shaft coupler and mounting may introduce offsets.

**Important:** The backend does not maintain an independent panel-angle calculation. The ESP32 is the single authority on current angle and publishes it in every telemetry and motor-state message.

---

## 6. Backend

**Language:** Python 3.11+  
**Framework:** FastAPI  
**Validation:** Pydantic v2  
**MQTT:** aiomqtt  
**Database:** SQLite (SQLAlchemy async + aiosqlite)

### Responsibilities

- Receive and validate MQTT messages
- Detect and log malformed / out-of-schema payloads
- Enqueue heavy work (DB writes, analytics) via async worker queues — the MQTT callback itself does minimal work
- Store telemetry, events, commands, solar state, predictions
- Calculate derived solar state (pvlib)
- Run rule-based fault detection
- Run ML predictions (Step 11)
- Expose REST API and WebSocket endpoint
- Feed the dashboard

### MQTT message handling flow

```
MQTT message arrives
        ↓
_on_message dispatcher (lightweight)
        ↓
parse device_id from topic
        ↓
route to handler (_handle_telemetry, _handle_status, ...)
        ↓
Pydantic validation
        ↓
put on async worker queue
        ↓
(worker) write to DB → run fault detection → update solar state
```

---

## 7. Database

**SQLite** in **WAL mode** for concurrent reads during writes.

### Tables

#### `devices`
Stores device configuration and identity.

| Column | Type | Notes |
|---|---|---|
| id | TEXT PK | e.g. `tracker-01` |
| name | TEXT | Human-readable name |
| latitude | REAL | For pvlib |
| longitude | REAL | For pvlib |
| altitude | REAL | metres |
| timezone | TEXT | e.g. `Asia/Kolkata` |
| firmware_version | TEXT | |
| created_at | DATETIME | UTC |
| updated_at | DATETIME | UTC |

#### `telemetry`
Raw measurements, one row per MQTT telemetry message.

Indexed on `(device_id, received_at)`.

#### `events`
Warnings, faults, sensor problems, communication anomalies.

| Column | Notes |
|---|---|
| event_type | `warning` / `error` / `critical` |
| code | e.g. `INA219_FAIL`, `TEPT_STUCK` |
| message | Human-readable description |

#### `commands`
Every control command for full auditability.

| Column | Notes |
|---|---|
| cmd_id | Unique, e.g. `CMD-83f21` |
| status | `pending` / `accepted` / `completed` / `rejected` / `expired` / `duplicate` / `failed` |
| ack_at | When ACK was received |

#### `solar_state`
Calculated solar geometry and tracking performance.

| Column | Notes |
|---|---|
| sun_azimuth_deg | From pvlib |
| sun_elevation_deg | From pvlib |
| target_panel_angle_deg | Derived from azimuth |
| tracking_error_deg | `target − actual` |

#### `predictions`
ML model outputs.

| Column | Notes |
|---|---|
| model_version | e.g. `rf-v1` |
| predicted_power_w | Model output |
| measured_power_w | Actual from INA219 |
| residual_w | `predicted − measured` |

---

## 8. Data Retention

SQLite must not grow unboundedly.

```
high-frequency raw telemetry (2 s interval)
              ↓
    keep for RAW_RETENTION_DAYS days (default 7)
              ↓
    aggregate to hourly/daily summaries
              ↓
    longer-term aggregated storage
```

Dashboard charts are **server-side downsampled** to a maximum of ~500 points per response to keep the frontend responsive.

---

## 9. Solar Calculations

**Library:** pvlib  
**Role:** Solar geometry only — not AI, not ML, not tracking control.

pvlib computes:
- Solar elevation angle
- Solar azimuth angle
- Clear-sky irradiance (Ineichen model)

The backend uses solar position to derive:
- **Target panel angle** (for a single-axis east–west tracker, target ≈ solar azimuth projected onto the tracker axis)
- **Tracking error** = target angle − actual panel angle (from ESP32 telemetry)

The ESP32 does **not** use pvlib. It tracks using the TEPT sensors directly. pvlib is for analytics and reference — not for commanding motor steps.

```
pvlib → solar position → target angle → tracking error (analytics)
TEPT sensors → real sunlight direction → ESP32 motor control (physical)
```

---

## 10. AI / ML Component

The genuine machine-learning component is **expected-power prediction**.

### Features (candidate inputs)
- Solar elevation angle
- Solar azimuth angle
- Time of day / day of year
- TEPT readings (proxy for effective irradiance)
- Panel angle

### Output
- Predicted power (W)

### Residual analysis
```
residual = predicted_power − measured_power
```

A persistently large residual may indicate:
- Panel shading
- Soiling / dirt on panel
- Sensor failure
- Mechanical tracking problem
- Electrical fault

### Model selection
- First candidate: `RandomForestRegressor` (scikit-learn)
- Model will be selected **after** collecting real hardware data for several days
- The algorithm is not locked before real measurements exist

### Libraries
- NumPy, pandas, scikit-learn, joblib

> **Optional:** Isolation Forest for unsupervised anomaly detection. May be compared against rule-based detection but does **not** replace it in the initial implementation.

---

## 11. Rule-Based Fault Detection

Deterministic rules run before (and independently of) any ML model. These make the first version straightforward to debug and defend in a viva.

| Rule | Trigger | Code |
|---|---|---|
| Stale telemetry | No telemetry received for > N seconds | `STALE_SENSOR` |
| TEPT stuck | A TEPT reading does not vary by > threshold over 30 s | `TEPT_STUCK` |
| TEPT saturated | A TEPT reading is at ADC maximum (4095) continuously | `TEPT_SATURATED` |
| INA219 failure | `ina219_ok == false` in telemetry | `INA219_FAIL` |
| Zero power during daylight | `power_w ≈ 0` when solar elevation > threshold | `UNDERVOLTAGE` |
| Motor stall | Mode is `go_to_angle` / `auto` but `panel_angle_deg` does not change | `MOTOR_STALL` |
| Sequence gap | `seq` jumps by more than 1 (dropped messages) | logged as event |
| ESP32 reboot | `uptime_ms` decreases | logged as event |
| Power sanity | `|power_w − voltage_v × current_a| > 10%` | logged as warning |

---

## 12. Frontend

**Technology:** React 18, TypeScript, Vite, Recharts  
**No CDN dependencies** — everything installed locally via npm.  
**Offline-capable** — served from local Vite dev server or static build.

### Dashboard layout

```
┌─────────────┬────────────┬──────────┬────────────┐
│ StatusCard  │ SensorCard │PowerCard │  Health    │
├─────────────┴────────────┴──────────┴────────────┤
│              LiveChart (power / V / A)            │
├───────────────────────────┬──────────────────────┤
│     AngleChart            │   ControlPanel       │
└───────────────────────────┴──────────────────────┘
```

### Live data flow

```
ESP32 → MQTT → Backend → WebSocket (/ws/live) → React state → charts
```

REST (`GET /api/live`, `GET /api/history`) is used for initial load and fallback polling.

---

## 13. REST + WebSocket Communication

See [`docs/api.md`](api.md) for the full API specification.

| Endpoint | Method | Auth | Purpose |
|---|---|---|---|
| `/api/live` | GET | — | Latest telemetry + solar state |
| `/api/history` | GET | — | Paginated historical telemetry |
| `/api/health` | GET | — | Service health status |
| `/api/analytics` | GET | — | Aggregated analytics |
| `/api/commands` | POST | API key | Send command to device |
| `/ws/live` | WS | — | Live push stream |

**Dashboard does not poll telemetry.** It subscribes to `/ws/live` and receives data as it arrives from MQTT.

---

## 14. Security Model

| Layer | Mechanism |
|---|---|
| MQTT | Username/password + ACL file; anonymous access disabled |
| Secrets | `.env` (backend) and `secrets.h` (firmware) — both gitignored |
| API auth | `X-API-Key` header required on `POST /api/commands` |
| CORS | Restricted to `http://localhost:5173` in development |
| FastAPI bind | `127.0.0.1` only during development (not `0.0.0.0`) |
| VCS | Only `.env.example` and `secrets.h.example` are committed |

---

## 15. Offline-First Philosophy

No cloud services. No internet dependency. The full stack runs on a single local machine or local network:

```
ESP32 ──Wi-Fi──► Mosquitto ──► Python backend ──► SQLite
                                    │
                               FastAPI + WS
                                    │
                            React Dashboard
```

This design is intentional:
- Works at college demonstrations without internet
- No dependency on external APIs (except pvlib which is a local Python library)
- Data stays on the local machine
- No subscription fees or cloud accounts
