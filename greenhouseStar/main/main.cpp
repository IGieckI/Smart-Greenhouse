#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_now.h"
#include "esp_netif.h"
#include "esp_mac.h"
#include "nvs_flash.h"
#include "esp_spiffs.h"
#include "TelemetryPacket.h"
#include "config.hpp"

#include "u8g2.h"
extern "C" {
    #include "u8g2_esp32_hal.h"
}

static const char *TAG = "CENTRAL_NODE";
#define DATA_FILE "/spiffs/telemetry.jsonl"

// FreeRTOS Queue
QueueHandle_t telemetry_queue;

// Global display object
u8g2_t u8g2;

// Callback function executed when data is received
void OnDataRecv(const esp_now_recv_info_t *esp_now_info, const uint8_t *incomingData, int len) {
    telemetry_packet_t myData;
    if (len == sizeof(myData)) {
        memcpy(&myData, incomingData, sizeof(myData));
        xQueueSend(telemetry_queue, &myData, 0);
    } else {
        ESP_LOGW(TAG, "Received packet of unexpected size: %d bytes", len);
    }
}

static void spiffs_init() {
    ESP_LOGI(TAG, "Initializing SPIFFS");
    esp_vfs_spiffs_conf_t conf = {
      .base_path = "/spiffs",
      .partition_label = NULL,
      .max_files = 5,
      .format_if_mount_failed = true
    };
    esp_err_t ret = esp_vfs_spiffs_register(&conf);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to mount or format SPIFFS");
    }
}

static void wifi_init() {
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());
}

static void display_init() {
    gpio_set_direction(VEXT_PIN, GPIO_MODE_OUTPUT);
    gpio_set_level(VEXT_PIN, 0); 
    vTaskDelay(pdMS_TO_TICKS(50));

    u8g2_esp32_hal_t u8g2_esp32_hal = U8G2_ESP32_HAL_DEFAULT;
    u8g2_esp32_hal.bus.i2c.sda = OLED_SDA;
    u8g2_esp32_hal.bus.i2c.scl = OLED_SCL;
    u8g2_esp32_hal.reset = OLED_RST;
    u8g2_esp32_hal_init(u8g2_esp32_hal);

    u8g2_Setup_ssd1306_i2c_128x64_noname_f(
        &u8g2, U8G2_R0, u8g2_esp32_i2c_byte_cb, u8g2_esp32_gpio_and_delay_cb);
    
    u8g2_InitDisplay(&u8g2);
    u8g2_SetPowerSave(&u8g2, 0);
    u8g2_ClearBuffer(&u8g2);
    u8g2_SetFont(&u8g2, u8g2_font_helvR08_tr); 
    u8g2_DrawStr(&u8g2, 0, 15, "Waiting for data...");
    u8g2_SendBuffer(&u8g2);
}

extern "C" void app_main(void)
{
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        nvs_flash_init();
    }

    spiffs_init();
    telemetry_queue = xQueueCreate(10, sizeof(telemetry_packet_t));
    display_init();
    wifi_init();

    if (esp_now_init() != ESP_OK) {
        ESP_LOGE(TAG, "Error initializing ESP-NOW");
        return;
    }
    esp_now_register_recv_cb(OnDataRecv);

    telemetry_packet_t displayData;
    char buffer[32];

    while (true) {
        // Wait for data, but unblock every 100ms to check the timer
        if (xQueueReceive(telemetry_queue, &displayData, pdMS_TO_TICKS(100))) {
            
            // Write Data to SPIFFS File (JSONL)
            FILE* f = fopen(DATA_FILE, "a");
            if (f != NULL) {
                fprintf(f, "{\"node_id\":%d, \"water_temp\":%.2f, \"tds\":%.0f, \"soil_moisture\":%.1f, \"light_lux\":%.2f, \"air_temp\":%.2f, \"humidity\":%.2f, \"pressure\":%.2f}\n",
                    displayData.node_id, displayData.water_temp, displayData.tds_value,
                    displayData.soil_moisture, displayData.light_lux, displayData.air_temp,
                    displayData.humidity, displayData.pressure);
                fclose(f);
            }

            // Update OLED
            u8g2_ClearBuffer(&u8g2);
            sprintf(buffer, "Pres: %.1f Pa", displayData.pressure);
            u8g2_DrawStr(&u8g2, 0, 10, buffer);
            sprintf(buffer, "Water Temp: %.1f C", displayData.water_temp);
            u8g2_DrawStr(&u8g2, 0, 22, buffer);
            sprintf(buffer, "Lux: %.1f TDS: %.0f ppm", displayData.light_lux, displayData.tds_value);
            u8g2_DrawStr(&u8g2, 0, 34, buffer);
            sprintf(buffer, "Soil Moist: %.1f%%", displayData.soil_moisture);
            u8g2_DrawStr(&u8g2, 0, 46, buffer);
            sprintf(buffer, "Air: %.1fC / %.1f%%", displayData.air_temp, displayData.humidity);
            u8g2_DrawStr(&u8g2, 0, 58, buffer);
            u8g2_SendBuffer(&u8g2);
            
            printf("ESP32: Packet Received.\n");
        }
    }
}