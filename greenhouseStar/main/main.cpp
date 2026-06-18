#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/ringbuf.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_now.h"
#include "esp_netif.h"
#include "esp_mac.h"
#include "nvs_flash.h"
#include "esp_heap_caps.h"
#include "TelemetryPacket.h"
#include "config.hpp"

#include "u8g2.h"
extern "C" {
    #include "u8g2_esp32_hal.h"
}

static const char *TAG = "GREENHOUSE_STAR";

RingbufHandle_t telemetry_ringbuf = NULL;
QueueHandle_t telemetry_queue;
u8g2_t u8g2;


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
    gpio_set_direction((gpio_num_t)VEXT_PIN, GPIO_MODE_OUTPUT);
    gpio_set_level((gpio_num_t)VEXT_PIN, 0); 
    vTaskDelay(pdMS_TO_TICKS(50));

    u8g2_esp32_hal_t u8g2_esp32_hal = U8G2_ESP32_HAL_DEFAULT;
    u8g2_esp32_hal.bus.i2c.sda = (gpio_num_t)OLED_SDA;
    u8g2_esp32_hal.bus.i2c.scl = (gpio_num_t)OLED_SCL;
    u8g2_esp32_hal.reset = (gpio_num_t)OLED_RST;
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


// ESP-NOW Callback
void OnDataRecv(const esp_now_recv_info_t *esp_now_info, const uint8_t *incomingData, int len) {
    telemetry_packet_t myData;
    if (len == sizeof(myData)) {
        memcpy(&myData, incomingData, sizeof(myData));
        // Push to queue; non-blocking with 0 ticks to safely execute inside the ISR context
        xQueueSendFromISR(telemetry_queue, &myData, NULL);
    } else {
        ESP_LOGW(TAG, "Received packet of unexpected size: %d bytes", len);
    }
}


// Task 1: Data Reception & Buffer Management
void reception_task(void *pvParameters) {
    telemetry_packet_t incomingData;
    ESP_LOGI(TAG, "Reception Task Started.");

    while (true) {
        // Block indefinitely until data arrives from the ESP-NOW callback
        if (xQueueReceive(telemetry_queue, &incomingData, portMAX_DELAY)) {
            
            // Attempt to write to Ring Buffer
            UBaseType_t res = xRingbufferSend(telemetry_ringbuf, &incomingData, sizeof(telemetry_packet_t), 0);
            
            if (res != pdTRUE) {
                // Buffer is full. Manually pop the oldest item to make room.
                size_t dummy_size;
                void *old_item = xRingbufferReceive(telemetry_ringbuf, &dummy_size, 0);
                if (old_item) {
                    vRingbufferReturnItem(telemetry_ringbuf, old_item); 
                    // Retry sending the new data
                    xRingbufferSend(telemetry_ringbuf, &incomingData, sizeof(telemetry_packet_t), 0);
                }
            }
        }
    }
}


// Task 2: Data Processing & Display Management
void data_manager_task(void *pvParameters) {
    size_t item_size;
    char buffer[32];
    ESP_LOGI(TAG, "Data Manager Task Started.");

    while (true) {
        // Wait indefinitely until data is available in the ring buffer
        telemetry_packet_t *item = (telemetry_packet_t *)xRingbufferReceive(telemetry_ringbuf, &item_size, portMAX_DELAY);
        
        if (item != NULL) {
            // Update OLED Display
            u8g2_ClearBuffer(&u8g2);
            sprintf(buffer, "Pres: %.1f Pa", item->pressure);
            u8g2_DrawStr(&u8g2, 0, 10, buffer);
            sprintf(buffer, "Water Temp: %.1f C", item->water_temp);
            u8g2_DrawStr(&u8g2, 0, 22, buffer);
            sprintf(buffer, "Lux: %.1f TDS: %.0f ppm", item->light_lux, item->tds_value);
            u8g2_DrawStr(&u8g2, 0, 34, buffer);
            sprintf(buffer, "Soil Moist: %.1f%%", item->soil_moisture);
            u8g2_DrawStr(&u8g2, 0, 46, buffer);
            sprintf(buffer, "Air: %.1fC / %.1f%%", item->air_temp, item->humidity);
            u8g2_DrawStr(&u8g2, 0, 58, buffer);
            u8g2_SendBuffer(&u8g2);

            // BACKGROUND WORK (LoRa etc)

            vRingbufferReturnItem(telemetry_ringbuf, (void *)item);
        }
    }
}

extern "C" void app_main(void)
{
    // Initialize NVS
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        nvs_flash_init();
    }

    // Allocate Ring Buffer in INTERNAL SRAM
    ESP_LOGI(TAG, "Allocating 16KB Ring Buffer in Internal SRAM...");
    uint8_t *ringbuf_storage = (uint8_t *)heap_caps_malloc(INTERNAL_BUFFER_SIZE, MALLOC_CAP_INTERNAL);
    StaticRingbuffer_t *ringbuf_struct = (StaticRingbuffer_t *)heap_caps_malloc(sizeof(StaticRingbuffer_t), MALLOC_CAP_8BIT);

    if (ringbuf_storage == NULL || ringbuf_struct == NULL) {
        ESP_LOGE(TAG, "CRITICAL: Failed to allocate Ring Buffer in Internal SRAM!");
        return; 
    }

    telemetry_ringbuf = xRingbufferCreateStatic(INTERNAL_BUFFER_SIZE, RINGBUF_TYPE_NOSPLIT, ringbuf_storage, ringbuf_struct);
    telemetry_queue = xQueueCreate(20, sizeof(telemetry_packet_t));

    // Initialize Subsystems
    display_init();
    wifi_init();

    if (esp_now_init() != ESP_OK) {
        ESP_LOGE(TAG, "Error initializing ESP-NOW");
        return;
    }
    esp_now_register_recv_cb(OnDataRecv);

    ESP_LOGI(TAG, "Central Node Initialized. Spawning tasks...");

    xTaskCreatePinnedToCore(
        reception_task,
        "Telemetry_Reception",
        4096,
        NULL,
        5,
        NULL,
        0
    );

    xTaskCreatePinnedToCore(
        data_manager_task,
        "Telemetry_Manager",
        4096,
        NULL,
        4,
        NULL,
        1
    );

    vTaskDelete(NULL);
}