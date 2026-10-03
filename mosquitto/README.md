# Mosquitto MQTT Broker — Solar Tracker Setup

## 1. Install Mosquitto

```bash
sudo apt update
sudo apt install -y mosquitto mosquitto-clients
```

## 2. Create password file

```bash
# Create file and add ESP32 device user
sudo mosquitto_passwd -c /etc/mosquitto/passwd solartracker

# Add backend/simulator user (no -c flag — would overwrite existing)
sudo mosquitto_passwd /etc/mosquitto/passwd backend
```

Set permissions:
```bash
sudo chmod 600 /etc/mosquitto/passwd
sudo chown mosquitto:mosquitto /etc/mosquitto/passwd
```

## 3. Install config and ACL

```bash
sudo cp mosquitto/mosquitto.conf /etc/mosquitto/mosquitto.conf
sudo cp mosquitto/acl /etc/mosquitto/acl
sudo chown mosquitto:mosquitto /etc/mosquitto/acl
```

## 4. Start broker

```bash
sudo systemctl restart mosquitto
sudo systemctl enable mosquitto
sudo systemctl status mosquitto
```

## 5. Test

Open two terminals.

**Terminal 1 — subscribe as backend:**
```bash
mosquitto_sub -h localhost -p 1883 \
  -u backend -P <backend_password> \
  -t "solar/#" -v
```

**Terminal 2 — publish test telemetry as tracker:**
```bash
mosquitto_pub -h localhost -p 1883 \
  -u solartracker -P <tracker_password> \
  -t "solar/tracker-01/telemetry" \
  -m '{"schema_version":1,"seq":1,"uptime_ms":1000,"voltage_v":8.12,"current_a":0.31,"power_w":2.52,"panel_angle_deg":45.0,"motor_steps":25,"tept_top":2740,"tept_bottom":2310,"tept_left":2630,"tept_right":2590,"ina219_ok":true,"sensors_ok":true,"mode":"auto"}'
```

You should see the message appear in Terminal 1.

## 6. Test with MQTT Explorer

[MQTT Explorer](http://mqtt-explorer.com/) is a useful GUI tool for inspecting topics and payloads during development. Connect to `localhost:1883` with the `backend` credentials.
