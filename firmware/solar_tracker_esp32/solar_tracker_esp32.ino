/*
 * =========================================================================
 * AI-Enabled Smart Solar Tracking and Energy Monitoring System
 * Controller: ESP32 DevKit V1 (38-Pin)
 * Framework: Arduino IDE
 * =========================================================================
 * 
 * LIBRARIES TO INSTALL IN ARDUINO IDE (Tools -> Manage Libraries):
 * 1. "PubSubClient" by Nick O'Leary
 * 2. "ArduinoJson" by Benoit Blanchon (version 6 or 7)
 * 3. "Adafruit INA219" by Adafruit
 * 4. "AccelStepper" by Mike McCauley
 * 
 * BOARD SELECTION:
 * Tools -> Board -> ESP32 Arduino -> "DOIT ESP32 DEVKIT V1" (or "ESP32 Dev Module")
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <Adafruit_INA219.h>
#include <AccelStepper.h>

// =========================================================================
// 1. YOUR NETWORK CREDENTIALS
// =========================================================================
const char* WIFI_SSID     = "YOUR_WIFI_NAME";      // Change to your Wi-Fi name
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";  // Change to your Wi-Fi password
const char* MQTT_SERVER   = "192.168.1.100";       // IP of the laptop running Mosquitto / backend
const int   MQTT_PORT     = 1883;
const char* MQTT_USER     = "solartracker";
const char* MQTT_PASS     = "changeme";

const char* DEVICE_ID     = "tracker-01";

// =========================================================================
// 2. PIN DEFINITIONS
// =========================================================================
// A4988 Stepper Driver pins
#define PIN_STEP    26
#define PIN_DIR     25
#define PIN_ENABLE  27

// TEPT4400 Light Sensors (ADC1 only to avoid Wi-Fi conflicts)
#define PIN_TEPT_TOP    36  // VP
#define PIN_TEPT_BOTTOM 39  // VN
#define PIN_TEPT_LEFT   34
#define PIN_TEPT_RIGHT  35

// I2C for INA219
#define PIN_I2C_SDA 21
#define PIN_I2C_SCL 22

// =========================================================================
// 3. TRACKER CONSTANTS & OBJECTS
// =========================================================================
const float DEGREES_PER_STEP  = 1.8;  // NEMA17 standard step angle
const float MAX_ANGLE         = 85.0;
const float MIN_ANGLE         = -85.0;
const int   LIGHT_DEADBAND    = 60;   // Sensitivity threshold (ADC counts)

// Operating Modes
enum Mode { MODE_AUTO, MODE_MANUAL, MODE_IDLE, MODE_FAULT };
Mode currentMode = MODE_AUTO;

AccelStepper stepper(AccelStepper::DRIVER, PIN_STEP, PIN_DIR);
Adafruit_INA219 ina219;
WiFiClient espClient;
PubSubClient mqtt(espClient);

// State variables
unsigned long seqNumber = 0;
unsigned long lastTelemetryTime = 0;
float currentAngle = 0.0;
bool ina219Ready = false;

// =========================================================================
// 4. FUNCTION DECLARATIONS
// =========================================================================
void connectWiFi();
void connectMQTT();
void mqttCallback(char* topic, byte* payload, unsigned int length);
void readSensors(int &top, int &bot, int &left, int &right);
void trackSun();
void sendTelemetry();

// =========================================================================
// SETUP
// =========================================================================
void setup() {
  Serial.begin(115200);
  Serial.println("\n--- Starting Solar Tracker ESP32 ---");

  // Setup Stepper
  pinMode(PIN_ENABLE, OUTPUT);
  digitalWrite(PIN_ENABLE, LOW); // LOW = Enabled on A4988
  stepper.setMaxSpeed(600.0);
  stepper.setAcceleration(300.0);

  // Setup I2C and INA219
  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);
  if (ina219.begin()) {
    ina219Ready = true;
    Serial.println("[OK] INA219 Sensor Initialized");
  } else {
    Serial.println("[ERR] INA219 not detected! Check 5V/GND/SDA/SCL wiring.");
  }

  // Connect Wi-Fi
  connectWiFi();

  // Configure MQTT
  mqtt.setServer(MQTT_SERVER, MQTT_PORT);
  mqtt.setCallback(mqttCallback);
}

// =========================================================================
// MAIN LOOP
// =========================================================================
void loop() {
  // Keep Wi-Fi and MQTT connected
  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
  }
  if (!mqtt.connected()) {
    connectMQTT();
  }
  mqtt.loop();

  // 1. Run automatic sunlight tracking if in AUTO mode
  if (currentMode == MODE_AUTO) {
    trackSun();
  }

  // Let stepper step non-blockingly
  stepper.run();

  // Update calculated angle from motor position
  currentAngle = stepper.currentPosition() * DEGREES_PER_STEP;

  // 2. Publish Telemetry every 2 seconds
  if (millis() - lastTelemetryTime >= 2000) {
    lastTelemetryTime = millis();
    sendTelemetry();
  }
}

// =========================================================================
// SENSORS & TRACKING LOGIC
// =========================================================================
void readSensors(int &top, int &bot, int &left, int &right) {
  // Read and average 8 samples to reduce noise
  long t = 0, b = 0, l = 0, r = 0;
  for (int i = 0; i < 8; i++) {
    t += analogRead(PIN_TEPT_TOP);
    b += analogRead(PIN_TEPT_BOTTOM);
    l += analogRead(PIN_TEPT_LEFT);
    r += analogRead(PIN_TEPT_RIGHT);
    delay(2);
  }
  top   = t / 8;
  bot   = b / 8;
  left  = l / 8;
  right = r / 8;
}

void trackSun() {
  // Only adjust when stepper has completed its previous move
  if (stepper.distanceToGo() != 0) return;

  int top, bot, left, right;
  readSensors(top, bot, left, right);

  // Single-axis horizontal tracking (Left vs Right differential)
  int error = left - right;

  if (abs(error) > LIGHT_DEADBAND) {
    // If Left has more light, turn towards left; otherwise turn right
    int stepDirection = (error > 0) ? -4 : 4; 
    long targetPos = stepper.currentPosition() + stepDirection;
    float targetAngle = targetPos * DEGREES_PER_STEP;

    // Safety Angle Limit Check (-85 to +85 degrees)
    if (targetAngle >= MIN_ANGLE && targetAngle <= MAX_ANGLE) {
      stepper.moveTo(targetPos);
    }
  }
}

// =========================================================================
// TELEMETRY (SEND TO BACKEND)
// =========================================================================
void sendTelemetry() {
  int top, bot, left, right;
  readSensors(top, bot, left, right);

  float voltage_v = ina219Ready ? ina219.getBusVoltage_V() : 0.0;
  float current_a = ina219Ready ? (ina219.getCurrent_mA() / 1000.0) : 0.0;
  float power_w   = voltage_v * current_a;

  // Build JSON payload
  StaticJsonDocument<350> doc;
  doc["schema_version"]  = 1;
  doc["seq"]             = seqNumber++;
  doc["uptime_ms"]       = millis();
  doc["voltage_v"]       = round(voltage_v * 100.0) / 100.0;
  doc["current_a"]       = round(current_a * 1000.0) / 1000.0;
  doc["power_w"]         = round(power_w * 100.0) / 100.0;
  doc["panel_angle_deg"] = round(currentAngle * 10.0) / 10.0;
  doc["motor_steps"]     = stepper.currentPosition();
  doc["tept_top"]        = top;
  doc["tept_bottom"]     = bot;
  doc["tept_left"]       = left;
  doc["tept_right"]      = right;
  doc["ina219_ok"]       = ina219Ready;
  doc["sensors_ok"]      = true;
  doc["mode"]            = (currentMode == MODE_AUTO) ? "auto" : "manual";

  char jsonBuffer[350];
  serializeJson(doc, jsonBuffer);

  char topic[64];
  snprintf(topic, sizeof(topic), "solar/%s/telemetry", DEVICE_ID);
  mqtt.publish(topic, jsonBuffer);
}

// =========================================================================
// WI-FI & MQTT MANAGEMENT
// =========================================================================
void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.print("Connecting to Wi-Fi");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    delay(500);
    Serial.print(".");
    attempts++;
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n[OK] Wi-Fi Connected! IP: " + WiFi.localIP().toString());
  } else {
    Serial.println("\n[WARN] Wi-Fi failed to connect. Retrying...");
  }
}

void connectMQTT() {
  if (mqtt.connected()) return;
  Serial.print("Connecting to MQTT Broker...");

  char statusTopic[64];
  snprintf(statusTopic, sizeof(statusTopic), "solar/%s/status", DEVICE_ID);

  // Last Will: notify backend if ESP32 unexpectedly disconnects
  const char* willPayload = "{\"state\":\"offline\"}";

  if (mqtt.connect(DEVICE_ID, MQTT_USER, MQTT_PASS, statusTopic, 1, true, willPayload)) {
    Serial.println(" Connected!");
    mqtt.publish(statusTopic, "{\"state\":\"online\"}", true);

    // Subscribe to commands from website/backend
    char cmdTopic[64];
    snprintf(cmdTopic, sizeof(cmdTopic), "solar/%s/cmd", DEVICE_ID);
    mqtt.subscribe(cmdTopic);
  } else {
    Serial.print(" Failed (rc=");
    Serial.print(mqtt.state());
    Serial.println("). Retrying in 5 seconds...");
    delay(2000);
  }
}

// =========================================================================
// COMMANDS FROM WEBSITE / BACKEND
// =========================================================================
void mqttCallback(char* topic, byte* payload, unsigned int length) {
  StaticJsonDocument<256> doc;
  deserializeJson(doc, payload, length);

  const char* cmd = doc["command"];
  if (!cmd) return;

  if (strcmp(cmd, "set_mode") == 0) {
    const char* modeStr = doc["params"]["mode"];
    if (strcmp(modeStr, "auto") == 0) currentMode = MODE_AUTO;
    if (strcmp(modeStr, "manual") == 0) currentMode = MODE_MANUAL;
    if (strcmp(modeStr, "idle") == 0) currentMode = MODE_IDLE;
  } 
  else if (strcmp(cmd, "go_to_angle") == 0) {
    currentMode = MODE_MANUAL;
    float targetAngle = doc["params"]["angle_deg"];
    if (targetAngle >= MIN_ANGLE && targetAngle <= MAX_ANGLE) {
      stepper.moveTo(targetAngle / DEGREES_PER_STEP);
    }
  }
  else if (strcmp(cmd, "stop") == 0) {
    currentMode = MODE_IDLE;
    stepper.stop();
  }
}
