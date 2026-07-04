# Smart Greenhouse

A distributed IoT system for monitoring greenhouse environments. Sensor nodes transmit data wirelessly to a central hub, which relays it to a backend for storage, visualization, and ML-based analysis.


## System Architecture

```
[Greenhouse Nodes]  ──ESP-NOW──►  [greenhouseStar]  ──LoRa──►  [loraWANGateway]  ──MQTT──►  [services]
  (ESP32)                           (ESP32)                        (ESP32)                        │
                                       │                                                    CoAP  │
                                       └──CoAP Observe ──────────────────────────────────────────►│
                                       │
                                       └──CoAP /dump ─────────────────────────────────►[operatorTools]

── Actuation (downlink) ──────────────────────────────────────────────────────────────────────────
[services] ──MQTT──► [loraWANGateway] ──LoRa──► [greenhouseStar] ──ESP-NOW──► [Greenhouse Nodes]
```

**Telemetry paths (uplink):**
1. **LoRa path** — Star → LoRaWAN Gateway → MQTT broker → `lw-client` → Controller → InfluxDB
2. **CoAP path** — Star → `cw-client` (if laptop is on the Star's Wi-Fi AP) → Controller → InfluxDB
3. **Offline path** — Star keeps a RAM ring buffer; operator downloads it via `operatorTools`

**Actuation path (downlink):**
4. **Command path** — Controller publishes to MQTT → Gateway TXs via LoRa → Star receives and queues → delivered to Node via ESP-NOW on next wakeup

---

## Prerequisites (all components)

| Tool | Purpose | Install |
|------|---------|---------|
| ESP-IDF v5.x | Firmware build & flash | https://docs.espressif.com/projects/esp-idf/en/stable/get-started/ |
| Docker + Compose v2 | Backend services | https://docs.docker.com/get-docker/ |
| Python 3.10+ | operatorTools | https://python.org |

---

## 1. greenhouseNode

An ESP32 that reads sensors, applies a median filter over 5 samples, and transmits a binary `TelemetryPacket` to the Star via ESP-NOW, then deep-sleeps for 2 minutes.

### Sensors

| Sensor | Interface | Notes |
|--------|-----------|-------|
| AHT20 + BMP280 | I2C (SDA=GPIO21, SCL=GPIO22) | Air temp, humidity, pressure |
| BH1750 | I2C (same bus) | Light (lux) |
| DS18B20 | 1-Wire | Water temperature |
| TDS (analog) | ADC1 CH0 (GPIO36) | Total dissolved solids |
| Soil Moisture (analog) | ADC1 CH5 (GPIO33) | Capacitive sensor |
| ADS1115 + thermocouple | I2C 0x48 | Leaf temperature — **optional** |

### Configuration

**`greenhouseNode/main/personal_config.hpp`** — edit before flashing:

```cpp
// Calibrate by measuring the ADC value when the sensor is dry and fully submerged
#define SOIL_MOISTURE_DRY_VAL  4000
#define SOIL_MOISTURE_WET_VAL  1050

// DS18B20 data pin
#define DS18B20_DATA_PIN  GPIO_NUM_32

// Actuator pin assignments — one #define per actuator
// Names must match ACTUATOR_TABLE in config.hpp (max 4 chars)
#define PIN_PUMP   GPIO_NUM_2

// Optional: ADS1115 for leaf temperature via thermocouple
// Comment this out if you don't have the ADS1115 wired up
#define ADS1115_ADDR  0x48

// MAC address of the Star node — read from Star's serial log at boot
static uint8_t central_mac[6] = {0x3C, 0x0F, 0x02, 0xEB, 0x8A, 0x5C};
```

> **How to find the Star's MAC:** Flash the Star first, open its serial monitor, and look for the line:
> `MAC Address (Wi-Fi STA): XX:XX:XX:XX:XX:XX`

**`greenhouseNode/main/config.hpp`** — timing (change if needed):

```cpp
#define NUM_SAMPLES      5      // median filter samples
#define SAMPLE_DELAY_MS  100    // delay between samples
#define DEEP_SLEEP_MS    120000 // 2 minutes between transmissions
```

### Build & Flash

```bash
cd greenhouseNode
idf.py set-target esp32
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor
```

---

## 2. greenhouseStar

The central ESP32 hub. Receives packets from all Nodes via ESP-NOW, then:
- Transmits via **LoRa** (primary uplink, includes its own `star_id` in every payload)
- Listens for **LoRa downlink commands** from the Gateway and forwards them to the target Node via ESP-NOW
- Serves a **CoAP observable** endpoint for nearby operators
- Maintains an in-RAM **ring buffer** downloadable over CoAP
- Displays live data on the **OLED** (Heltec boards only)

> **`star_id`** is derived from the last 4 bytes of the Star's Wi-Fi STA MAC address (same formula as `node_id` on Nodes). It is printed at boot: `Star ID: XXXXXXXX`. The backend uses this to route commands to the correct Star.

### Configuration

**`greenhouseStar/main/config.hpp`**:

```cpp
// If running on a Heltec V3 board (has built-in OLED + LoRa)
// Comment this line out if using a generic ESP32 with external LoRa module
#define IS_HELTEC

// RAM ring buffer — holds ~12 sensor readings before overwriting oldest
#define INTERNAL_BUFFER_SIZE (16 * 1024)

// LoRa hardware pins — already set for Heltec V3, change if using a different board
#define LORA_CS    8
#define LORA_SCK   9
// ... (see config.hpp for all pins)
```

### Wi-Fi AP

The Star creates a hotspot at boot:
- **SSID:** `GREENHOUSE_STAR`
- **Password:** `operator123`
- **IP:** `192.168.4.1`

The operator laptop and the `cw-client` Docker container both use this AP.

### Endpoints

All operator/backend access is over **CoAP** (UDP 5683); the Star runs no HTTP server.

| Endpoint | Protocol | Description |
|----------|----------|-------------|
| `coap://192.168.4.1/telemetry` | CoAP Observe (GET) | Stream binary `TelemetryPacket` to subscribers |
| `coap://192.168.4.1/dump` | CoAP GET | Download ring buffer as packed binary `TelemetryPacket` records (block-wise) |
| `coap://192.168.4.1/info` | CoAP GET | Returns `{"star_id":…}` as JSON |
| `coap://192.168.4.1/set_time` | CoAP POST | Sync RTC (Unix timestamp as ASCII in payload) |
| `coap://192.168.4.1/command` | CoAP POST | Queue an actuation command (JSON) for a Node |

### Build & Flash

```bash
cd greenhouseStar
idf.py set-target esp32s3   # Heltec V3 uses ESP32-S3
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor
```

### LoRa Parameters

| Parameter | Value |
|-----------|-------|
| Frequency | 868.0 MHz |
| Bandwidth | 125 kHz |
| Spreading Factor | SF9 |
| Coding Rate | CR7 |
| Sync Word | 0x12 |

These must match identically in the Gateway.

---

## 3. loraWANGateway

An ESP32 (Heltec V3) that bridges LoRa ↔ MQTT. It forwards telemetry uplink (Star → MQTT) and relays actuation commands downlink (MQTT → Star via LoRa). When a command is received via MQTT, the Gateway TXs it once via LoRa and then returns to continuous interrupt-driven receive, reacting whenever an ACK arrives from the Star rather than waiting on a fixed timeout. If the Star ACKs, the ACK is forwarded to `greenhouse/acks` on the broker so the controller can stop retrying. If no ACK arrives, the controller retries (up to 3 attempts, 3 s apart).

### Configuration

**`loraWANGateway/main/config.hpp`** — the only file you need to edit:

```cpp
// Your home/lab Wi-Fi network
#define WIFI_SSID  "your_network_ssid"
#define WIFI_PASS  "your_network_password"

// Broker selection: comment out USE_LOCAL_BROKER to fall back to HiveMQ
#define USE_LOCAL_BROKER

#ifdef USE_LOCAL_BROKER
    // LAN IP of the machine running docker-compose
    // Find it with: ip route get 1 | awk '{print $7}' (Linux)
    //            or: ipconfig (Windows) / ifconfig (macOS)
    #define MQTT_BROKER  "mqtt://192.168.1.X"
#else
    #define MQTT_BROKER  "mqtt://broker.hivemq.com"
#endif
```

> **Note:** The gateway must be on the same LAN as the machine running `docker-compose`. The services stack exposes port `1883` — the gateway connects to that port on your machine's LAN IP.

### Build & Flash

```bash
cd loraWANGateway
idf.py set-target esp32s3
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor
```

---

## 4. services

The backend stack running in Docker. Handles data ingestion from both the LoRa gateway and CoAP path, stores data in InfluxDB, exposes a Grafana dashboard, runs ML models, and provides a Telegram bot interface.

### Services Overview

| Container | Role | Port |
|-----------|------|------|
| `influxdb` | Time-series database | 8086 |
| `grafana` | Dashboard | 3030 |
| `mosquitto` | Local MQTT broker | 1883 |
| `controller` | Data router → InfluxDB | 3001 |
| `lw-client` | LoRa path: MQTT subscriber → controller | — |
| `cw-client` | CoAP path: observes Star → controller | — |
| `ml-trainer` | Trains anomaly-detection model on startup | 8001 |
| `ml-inference` | Serves predictions via HTTP | 8000 |
| `tg-bot` | Telegram bot: query data and predictions | — |

### Setup

```bash
cd services

# 1. First-time setup — creates .env template and checks Docker
./greenhouse.sh setup

# 2. Edit .env and fill in your Telegram bot token
#    (get one from @BotFather on Telegram)
nano .env
```

**.env file:**
```
INFLUX_TOKEN=secret_token
TELEGRAM_TOKEN=your_telegram_bot_token_here
```

> `INFLUX_TOKEN` must match `DOCKER_INFLUXDB_INIT_ADMIN_TOKEN` in `docker-compose.yml`. The default value `secret_token` works out of the box.

### Running the Stack

```bash
# Start everything (all services)
./greenhouse.sh up

# Start only core services (skip ML — faster startup for development)
./greenhouse.sh up core

# Start only ML pipeline
./greenhouse.sh up ml

# Stop everything
./greenhouse.sh down
```

### Grafana First Login

1. Open `http://localhost:3030`
2. Login: `admin` / `admin` (you'll be prompted to change the password)
3. If asked, change password into `adminadmin`
4. Add InfluxDB as a data source:
   - URL: `http://influxdb:8086`
   - Organization: `iot_org`
   - Token: `secret_token`
   - Bucket: `sensor_data`

### CoAP Path Setup

`cw-client` subscribes to the Star's CoAP endpoint. For this to work, the machine running `docker-compose` must be connected to the **Star's Wi-Fi AP** (`GREENHOUSE_STAR`):

1. Connect your laptop to `GREENHOUSE_STAR` (password: `operator123`)
2. The `cw-client` container will automatically reach `coap://192.168.4.1/telemetry`
3. At startup, `cw-client` queries `coap://192.168.4.1/info` to auto-discover the Star's `star_id` — no manual configuration needed

> When connected to the Star's AP, your machine loses internet access. The LoRa path (via a gateway on your home LAN) is the better option for permanent deployments.

### Useful Commands

```bash
# Follow all logs
./greenhouse.sh logs

# Follow logs for one service
./greenhouse.sh logs lw-client
./greenhouse.sh logs controller

# Rebuild and restart a single container after code changes
./greenhouse.sh reset cw-client

# Verify MQTT messages are reaching the broker
docker run --rm --network services_default eclipse-mosquitto:2 \
    mosquitto_sub -h mosquitto -t "greenhouse/telemetry/live"
```

### Sharing Data Between Instances

Two team members can merge their InfluxDB datasets using `exchange.sh`:

```bash
# Import a colleague's CSV export and produce a merged cumulative dump
./greenhouse.sh exchange /path/to/colleague_dump.csv
# Output: services/cumulative_dump.csv  (import this on another machine)
```

### Controller API

#### `POST /api/data`

Ingestion endpoint consumed by `lw-client` and `cw-client`. Both clients include `star_id` in the payload — the controller uses this to maintain `controller/data/topology.json`, which maps each `node_id` to the `star_id` of the Star it communicates through.

```
POST http://localhost:3001/api/data
Content-Type: application/json

{
  "star_id": "3C0F02EB",
  "node_id": 123456,
  "timestamp": 1750000000,
  "air_temp": 25.3,
  "humidity": 58.0,
  "pressure": 1013.2,
  "water_temp": 22.1,
  "soil_moisture": 62.0,
  "tds": 350.0,
  "light_lux": 4500.0,
  "leaf_temp": 26.1
}
```

#### `POST /api/command`

Sends an actuation command to a specific Node. The controller looks up the Node's Star in `topology.json` and publishes to `greenhouse/commands/<star_id>` on the MQTT broker. The loraWANGateway ESP32 (subscribed to that topic) picks it up and TXs it via LoRa — neither `lw-client` nor `cw-client` is involved.

The easiest way to trigger a command is via `greenhouse.sh`:

```bash
# Pump on full for 10 seconds
./greenhouse.sh command <node_id> pump 100 10

# LED at 75% brightness for 30 seconds
./greenhouse.sh command <node_id> led 75 30

# Pump off immediately
./greenhouse.sh command <node_id> pump 0 0
```

Raw API call if needed:

```
POST http://localhost:3001/api/command
Content-Type: application/json

{ "node_id": 123456, "actuator": "pump", "value": 100, "duration_s": 10 }
```

`value` is 0–100: binary actuators treat 0 as off and any non-zero as on; PWM actuators use it as duty cycle percentage.

Responses: `200 OK` with `{ status, star_id, node_id, topic }` | `404` if the Node has never been seen | `400` if fields are missing.

> **Note:** The Node delivers the command on its **next wakeup** (up to 2 minutes later). The command is queued in the Star's RAM until then.

#### Leaf Temperature Fallback

If `leaf_temp` is missing or below 5°C (this project born in summer, is strange to have temperature values below that threshold), the controller reads a fallback value from `controller/data/leaf_temp.txt`. Format: `3750846324/21.5/Any_String_you_WANT` (value / reading ID). The same reading is accepted for a limited number of uses before the controller discards it and alerts with audio beeps. This only to ensure into the raw data Influx bucket fresh data.

---

## 5. operatorTools

A GUI dashboard for direct interaction with the Star node while on-site (connected to `GREENHOUSE_STAR` Wi-Fi).

### Requirements

```bash
pip install -r requirements.txt
# tkinter is included in standard Python on most systems
# If missing on Linux: sudo apt install python3-tk
```

### Usage

```bash
cd operatorTools
python StarInterface.py
```

The tool auto-detects when the Star is reachable (probes `coap://192.168.4.1/info` every 3 seconds) and enables buttons accordingly.

### Features

| Button | Action |
|--------|--------|
| Sync & Download Data | Pulls all records from the Star's RAM ring buffer over CoAP (`/dump`, block-wise binary) |
| Sync Device Time | Pushes current system time to the Star's RTC (`/set_time`) |
| Export to CSV | Saves the downloaded table to a CSV file |

---

## Packet Formats

### TelemetryPacket

The binary struct sent from Node → Star via ESP-NOW, and from Star → `cw-client` via CoAP:

```c
typedef struct {
    uint32_t timestamp;     // Unix epoch (set by Star at reception)
    uint32_t node_id;       // Derived from last 4 bytes of Node MAC
    float    water_temp;    // °C
    float    tds_value;     // ppm
    float    soil_moisture; // % (0–100)
    float    light_lux;     // lux
    float    air_temp;      // °C
    float    humidity;      // %
    float    pressure;      // Pa
    float    leaf_temp;     // °C
} telemetry_packet_t;       // 40 bytes, little-endian
```

The Star's LoRa uplink JSON payload uses abbreviated keys to minimize airtime (also includes `sid` for Star identification):

```json
{"sid":1234567890,"ts":1750000000,"id":123456,"p":1013.2,"wt":22.1,"lux":4500,"tds":350,"sm":62,"at":25.3,"h":58,"lt":26.1}
```

`lw-client` maps these abbreviated keys back to the full field names before forwarding to the controller.

### CommandPacket

The binary struct sent from Star → Node via ESP-NOW after a downlink command is received:

```c
#define CMD_ACTUATOR_LEN 5  // max 4-char names + null terminator ("pump", "led", "fan")

typedef struct {
    uint32_t node_id;
    char     actuator[CMD_ACTUATOR_LEN];  // null-terminated name matching ACTUATOR_TABLE
    uint8_t  value;                        // 0=off, 1-100=level (binary: 0 or 100; PWM: duty %)
    uint16_t duration_s;                   // 0=hold indefinitely
} __attribute__((packed)) command_packet_t;  // 12 bytes
```

The LoRa downlink JSON (Gateway → Star) format:

```json
{"nid":123456,"act":"pump","val":100,"dur":10}
```

Actuators are defined in `greenhouseNode/main/personal_config.hpp` as an `ACTUATOR_TABLE` mapping names to GPIOs and control types (`ACT_BINARY` or `ACT_PWM`). The actuation logic in `main.cpp` is generic — adding a new actuator only requires a new row in the table.
