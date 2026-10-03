# ☀️ AI-Enabled Smart Solar Tracking and Energy Monitoring System Using ESP32

[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://python.org)
[![ESP32](https://img.shields.io/badge/Hardware-ESP32-E7352C?logo=espressif&logoColor=white)](https://espressif.com)
[![FastAPI](https://img.shields.io/badge/Backend-FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

An autonomous single-axis solar tracker combining **edge-level hardware autonomy** with **low-latency real-time web telemetry**, **physics-based solar calculations (pvlib)**, and an **in-memory Machine Learning model** for expected power prediction and fault/shading diagnostics.

---

## 🎯 Key Design Philosophy

> **The ESP32 tracks the sun independently.** The motor moves toward bright sunlight using differential phototransistor error on the microcontroller. The server/cloud is strictly for monitoring, analytics, and AI diagnostics — not for commanding every motor step. If the computer or network drops offline, the physical tracker continues operating safely.

---

## 🔌 Hardware Components & Pinout

| Component | Part / Model | Purpose |
| :--- | :--- | :--- |
| **Microcontroller** | ESP32 DevKit V1 (38-pin) | Embedded controller, sensor reading, motor control, MQTT |
| **Solar Panel** | SLG Solar Systems SLG-0603 (3 W, 8.7 V, 0.35 A) | Energy generation source |
| **Stepper Motor** | NEMA 17 Bipolar (1.8° / step, 42 mm frame) | Single-axis rotation |
| **Motor Driver** | A4988 (Full-step mode) | Stepper control (100 µF cap across VMOT & GND) |
| **Light Sensors** | 4 × TEPT4400 Phototransistors | Differential sunlight tracking (ADC1 pins only) |
| **Power Sensor** | INA219 (I²C) | High-side voltage, current, and power monitoring |
| **Power Supply** | 12 V, 2 A DC Adapter | Motor driver power |

### Pin Wiring Table

```
      ESP32 DevKit V1
      ┌───────────────────────┐
      │  GPIO 26  ──────────► │  A4988 STEP Pin
      │  GPIO 25  ──────────► │  A4988 DIR Pin
      │  GPIO 27  ──────────► │  A4988 ENABLE Pin (Active LOW)
      │                       │
      │  GPIO 36 (VP) ◄────── │  TEPT4400 Top Sensor (Analog)
      │  GPIO 39 (VN) ◄────── │  TEPT4400 Bottom Sensor (Analog)
      │  GPIO 34     ◄────── │  TEPT4400 Left Sensor (Analog)
      │  GPIO 35     ◄────── │  TEPT4400 Right Sensor (Analog)
      │                       │
      │  GPIO 21 (SDA) ◄────► │  INA219 SDA
      │  GPIO 22 (SCL) ◄────► │  INA219 SCL
      │  3V3 / GND    ─────── │  Sensors VCC & GND
      └───────────────────────┘
```
*(Note: ADC1 pins GPIO 34, 35, 36, 39 are used to prevent ADC2 conflicts when Wi-Fi is active).*

---

## 🧠 What is Actually "AI" in this Project?

We do not put the label "AI" on deterministic math. Here is the clear technical distinction:

| Component | Technology | AI / ML? | Role |
| :--- | :--- | :---: | :--- |
| **Sun Tracking** | C++ on ESP32 | ❌ | Differential light error (`left - right`) |
| **Motor Control** | AccelStepper | ❌ | Safe stepper acceleration & bounds |
| **Solar Position** | `pvlib` | ❌ | Deterministic solar geometry |
| **Web Dashboard** | HTML5 / WebSocket | ❌ | Live visualization (<15ms latency) |
| **Expected Power Prediction** | `scikit-learn` / ML | ✅ | **Predicts theoretical Watts under current sun conditions** |
| **Residual Anomaly Detection**| Python Backend | ✅ | **Flags shading, dirt/soiling, or misalignment when $P_{expected} - P_{measured} > \Delta$** |

---

## 🚀 Quick Start (Zero Terminal Hassle)

### 1. Running the Python Backend & Web Dashboard
In the project directory, run:
```bash
cd backend
source .venv/bin/activate
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
Open **`http://localhost:8000`** in your browser (or `http://<your-ip>:8000` on your phone).

### 2. Flashing the ESP32 (Arduino IDE)
1. Open the **Arduino IDE**.
2. Install 4 libraries via **Library Manager**:
   * `PubSubClient`, `ArduinoJson`, `Adafruit INA219`, `AccelStepper`
3. Open `firmware/solar_tracker_esp32/solar_tracker_esp32.ino`.
4. Enter your Wi-Fi name, password, and laptop IP address at the top.
5. Select board **DOIT ESP32 DEVKIT V1** and hit **Upload**!

### 3. Testing with the Python Simulator (No Hardware Needed)
You can test the entire backend, AI engine, and web dashboard without wiring physical hardware:
```bash
source backend/.venv/bin/activate
python simulator/src/simulator.py --cloud-factor 0.2
```

---

## 📊 System Architecture

```
                   SUN
                    ↓
        ┌──────────────────────┐
        │   Solar Panel (3W)   │
        └──────────┬───────────┘
                   │ Rotation
                NEMA 17 Stepper ◄── A4988 Driver
                   │                      ▲
                   ▼                      │
              ESP32 Controller ───────────┘
             ┌─────┴──────┐
        TEPT4400 ×4     INA219 (V/I/P)
             └─────┬──────┘
                   │ Wi-Fi (MQTT JSON)
                   ▼
         Mosquitto Broker (Port 1883)
                   │
                   ▼
         Python Backend (FastAPI)
         ┌─────────┴─────────┐
         │ In-Memory AI      │ ──► Expected Power vs Measured
         │ SQLite (WAL mode) │ ──► Microsecond History Persistence
         └─────────┬─────────┘
                   │ Low-Latency WebSocket (<15ms)
                   ▼
       Web Dashboard (HTML5 / JS)
     (Works on any phone/laptop on Wi-Fi)
```

---

## 🧪 Testing & Validation

The project includes an automated contract test suite verifying all MQTT JSON schemas:
```bash
backend/.venv/bin/pytest tests/contracts/ -v
```
Output:
```
tests/contracts/test_schemas.py ........................ [100%]
============================== 24 passed in 0.12s ==============================
```

---

## 📁 Repository Structure

```
solar-tracker/
├── README.md                      # Complete Project Documentation
├── .env.example                   # Template Environment Configurations
├── .gitignore                     # Git Exclusions
├── LICENSE                        # MIT Open Source License
│
├── firmware/
│   └── solar_tracker_esp32/
│       └── solar_tracker_esp32.ino  # Single-file Arduino IDE Sketch
│
├── backend/
│   ├── app/
│   │   ├── main.py                # FastAPI Server (Serves API + Dashboard)
│   │   ├── mqtt.py                # Asynchronous MQTT Client
│   │   ├── workers.py             # Low-Latency Queue & AI Analytics
│   │   ├── database.py            # SQLite WAL Connection Engine
│   │   ├── models.py              # SQLAlchemy 2.0 ORM Schema
│   │   └── api/routes.py          # REST & WebSocket Endpoints
│   ├── static/
│   │   ├── index.html             # High-Speed Offline Web Dashboard
│   │   └── chart.min.js           # Bundled Local Chart Engine
│   └── requirements.txt           # Python Backend Dependencies
│
├── simulator/
│   ├── requirements.txt
│   └── src/
│       └── simulator.py           # Synthetic ESP32 Data Generator
│
├── mosquitto/
│   ├── mosquitto.conf             # Local Broker Configuration
│   ├── acl                        # Access Control List
│   └── README.md                  # Broker Setup Guide
│
└── tests/
    ├── requirements.txt
    └── contracts/                 # Schema Contract Test Suite
```

---

## 📄 License
This project is open-source under the [MIT License](LICENSE).
