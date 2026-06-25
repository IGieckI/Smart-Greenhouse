// LoRa Hardware Pins (Heltec V3)
#define LORA_CS    8
#define LORA_SCK   9
#define LORA_MOSI  10
#define LORA_MISO  11
#define LORA_RST   12
#define LORA_BUSY  13
#define LORA_DIO1  14

// WiFi
#define WIFI_SSID  ""
#define WIFI_PASS  ""

// MQTT
#define MQTT_TOPIC  "greenhouse/telemetry/live"

// Broker selection: comment out USE_LOCAL_BROKER to use HiveMQ, otherwise a local broker will be used 
#define USE_LOCAL_BROKER

#ifdef USE_LOCAL_BROKER
    // IP of the machine running docker-compose (must be reachable from the ESP32's WiFi)
    #define MQTT_BROKER  "mqtt://192.168.1.7"
#else
    #define MQTT_BROKER  "mqtt://broker.hivemq.com"
#endif
