#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "esp_wifi.h"
#include "esp_log.h"
#include "esp_event.h"
#include "nvs_flash.h"
#include "mqtt_client.h"
#include <RadioLib.h>
#include "driver/spi_master.h"
#include "EspHal.h"
#include "config.hpp"

static const char *TAG = "LORA_GATEWAY";

EspHal* hal = nullptr;
Module* mod = nullptr;
SX1262* radio = nullptr;

static esp_mqtt_client_handle_t mqtt_client = NULL;
static EventGroupHandle_t wifi_event_group;
const int WIFI_CONNECTED_BIT = BIT0;


static void mqtt_event_handler(void *handler_args, esp_event_base_t base, int32_t event_id, void *event_data) {
    switch (event_id) {
        case MQTT_EVENT_CONNECTED:
            ESP_LOGI(TAG, "MQTT connected to %s", MQTT_BROKER);
            break;
        case MQTT_EVENT_DISCONNECTED:
            ESP_LOGW(TAG, "MQTT disconnected, will retry automatically");
            break;
        case MQTT_EVENT_ERROR:
            ESP_LOGE(TAG, "MQTT error");
            break;
        default:
            break;
    }
}

static void wifi_event_handler(void* arg, esp_event_base_t event_base, int32_t event_id, void* event_data) {
    if (event_id == WIFI_EVENT_STA_START || event_id == WIFI_EVENT_STA_DISCONNECTED) {
        esp_wifi_connect();
    } else if (event_id == IP_EVENT_STA_GOT_IP) {
        xEventGroupSetBits(wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

static void wifi_init_sta(void) {
    wifi_event_group = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL));

    wifi_config_t wifi_config = {};
    strcpy((char*)wifi_config.sta.ssid, WIFI_SSID);
    strcpy((char*)wifi_config.sta.password, WIFI_PASS);
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());
}


static void lora_rx_task(void *pvParameters) {
    ESP_LOGI(TAG, "LoRa RX task started, listening on 868 MHz");

    while (1) {
        uint8_t buf[256] = {0};
        int state = radio->receive(buf, sizeof(buf) - 1);

        if (state == RADIOLIB_ERR_NONE) {
            ESP_LOGI(TAG, "Packet received — RSSI: %.1f dBm, SNR: %.1f dB", radio->getRSSI(), radio->getSNR());
            ESP_LOGI(TAG, "Payload: %s", (char*)buf);

            if (mqtt_client != NULL) {
                esp_mqtt_client_publish(mqtt_client, MQTT_TOPIC, (char*)buf, 0, 0, 0);
                ESP_LOGI(TAG, "Forwarded to MQTT topic: %s", MQTT_TOPIC);
            }
        } else if (state != RADIOLIB_ERR_RX_TIMEOUT) {
            ESP_LOGE(TAG, "LoRa receive error, code: %d", state);
        }

        vTaskDelay(pdMS_TO_TICKS(10));
    }
}


extern "C" void app_main(void) {
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    wifi_init_sta();
    xEventGroupWaitBits(wifi_event_group, WIFI_CONNECTED_BIT, pdFALSE, pdFALSE, portMAX_DELAY);
    ESP_LOGI(TAG, "WiFi connected");

    esp_mqtt_client_config_t mqtt_cfg = {};
    mqtt_cfg.broker.address.uri = MQTT_BROKER;
    mqtt_client = esp_mqtt_client_init(&mqtt_cfg);
    esp_mqtt_client_register_event(mqtt_client, MQTT_EVENT_ANY, mqtt_event_handler, NULL);
    esp_mqtt_client_start(mqtt_client);

    hal = new EspHal(LORA_SCK, LORA_MISO, LORA_MOSI, SPI2_HOST, SPI_MASTER_FREQ_8M);
    mod = new Module(hal, LORA_CS, LORA_DIO1, LORA_RST, LORA_BUSY);
    radio = new SX1262(mod);

    // Must match greenhouseStar: 868 MHz, BW 125, SF9, CR7, sync 0x12
    if (radio->begin(868.0, 125.0, 9, 7, 0x12, 10, 8, 1.6, false) == RADIOLIB_ERR_NONE) {
        ESP_LOGI(TAG, "LoRa initialized successfully");
        xTaskCreatePinnedToCore(lora_rx_task, "lora_rx", 4096, NULL, 5, NULL, 1);
    } else {
        ESP_LOGE(TAG, "LoRa init failed");
    }
}
