# Smart Greenhouse

A distributed IoT system for monitoring greenhouse environments. Sensor nodes transmit data wirelessly to a central hub, which relays it to a backend for storage, visualization, and ML-based analysis.


## System Architecture

```
[Greenhouse Nodes]  ──ESP-NOW──►  [greenhouseStar]  ──LoRa──►  [loraWANGateway]  ──MQTT──►  [services]
  (ESP32)                           (ESP32)                        (ESP32)                        │
                                       │                                                    CoAP  │
                                       └──CoAP Observe ──────────────────────────────────────────►│
                                       │
                                       └──HTTP /dump ─────────────────────────────────►[operatorTools]
```

**Data paths:**
1. **LoRa path** — Star → LoRaWAN Gateway → MQTT broker → `lw-client` → Controller → InfluxDB
2. **CoAP path** — Star → `cw-client` (if laptop is on the Star's Wi-Fi AP) → Controller → InfluxDB
3. **Offline path** — Star keeps a RAM ring buffer; operator downloads it via `operatorTools`

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
- Transmits via **LoRa** (primary uplink)
- Serves a **CoAP observable** endpoint for nearby operators
- Maintains an in-RAM **ring buffer** accessible via HTTP
- Displays live data on the **OLED** (Heltec boards only)

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

| Endpoint | Protocol | Description |
|----------|----------|-------------|
| `192.168.4.1/dump` | HTTP GET | Download ring buffer as JSON array |
| `192.168.4.1/set_time` | HTTP POST | Sync RTC (sends Unix timestamp in body) |
| `coap://192.168.4.1/telemetry` | CoAP Observe | Stream binary `TelemetryPacket` to subscribers |

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

An ESP32 (Heltec V3) that listens for LoRa packets from the Star and forwards them to an MQTT broker over Wi-Fi.

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
| `ml-trainer` | Trains anomaly-detection model on startup | — |
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
INFLUX_TOKEN=TokenFittizio
TELEGRAM_TOKEN=your_telegram_bot_token_here
```

> `INFLUX_TOKEN` must match `DOCKER_INFLUXDB_INIT_ADMIN_TOKEN` in `docker-compose.yml`. The default value `TokenFittizio` works out of the box.

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
3. Add InfluxDB as a data source:
   - URL: `http://influxdb:8086`
   - Organization: `iot_org`
   - Token: `TokenFittizio`
   - Bucket: `sensor_data`

### CoAP Path Setup

`cw-client` subscribes to the Star's CoAP endpoint. For this to work, the machine running `docker-compose` must be connected to the **Star's Wi-Fi AP** (`GREENHOUSE_STAR`):

1. Connect your laptop to `GREENHOUSE_STAR` (password: `operator123`)
2. The `cw-client` container will automatically reach `coap://192.168.4.1/telemetry`

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

The controller exposes one endpoint consumed by both ingestion clients:

```
POST http://localhost:3001/api/data
Content-Type: application/json

{
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

The controller writes all numeric fields dynamically to InfluxDB under the `sensor_measurements` measurement, tagged with `id_board`.

#### Leaf Temperature Fallback

If `leaf_temp` is missing or below 5°C, the controller reads a fallback value from `controller/data/leaf_temp.txt`. Format: `21.5/A` (value / reading ID). The same reading is accepted for a limited number of uses before the controller discards it and alerts with audio beeps.

---

## 5. operatorTools

A GUI dashboard for direct interaction with the Star node while on-site (connected to `GREENHOUSE_STAR` Wi-Fi).

### Requirements

```bash
pip install requests
# tkinter is included in standard Python on most systems
# If missing on Linux: sudo apt install python3-tk
```

### Usage

```bash
cd operatorTools
python StarInterface.py
```

The tool auto-detects when the Star is reachable (checks port 80 every 3 seconds) and enables buttons accordingly.

### Features

| Button | Action |
|--------|--------|
| Sync & Download Data | Pulls all records from the Star's RAM ring buffer |
| Sync Device Time | Pushes current system time to the Star's RTC |
| Export to CSV | Saves the downloaded table to a CSV file |

---

## TelemetryPacket Format

The binary struct shared between all firmware components and the `cw-client`:

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

The LoRa JSON payload uses abbreviated keys to minimize packet size:

```json
{"ts":1750000000,"id":123456,"p":1013.2,"wt":22.1,"lux":4500,"tds":350,"sm":62,"at":25.3,"h":58,"lt":26.1}
```

`lw-client` maps these back to the full field names before forwarding to the controller.
