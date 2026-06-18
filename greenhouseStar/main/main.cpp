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
#include "esp_http_server.h"
#include "TelemetryPacket.h"
#include "config.hpp"
#include <sys/time.h>
#include <time.h>

#include "u8g2.h"
extern "C" {
    #include "u8g2_esp32_hal.h"
}

static const char *TAG = "GREENHOUSE_STAR";

// --- Global Handles ---
RingbufHandle_t telemetry_ringbuf = NULL;
QueueHandle_t telemetry_queue;
QueueHandle_t display_mailbox; 
u8g2_t u8g2;

// --- HTTP Server Endpoint Handler ---
esp_err_t download_data_handler(httpd_req_t *req) {
    ESP_LOGI(TAG, "Operator requested data dump via Wi-Fi...");

    // Set response type to JSON
    httpd_resp_set_type(req, "application/json");
    
    // Start chunked response (great for large buffers so we don't run out of RAM)
    httpd_resp_sendstr_chunk(req, "["); 

    size_t item_size;
    bool first = true;
    int items_dumped = 0;
    char json_buf[256];

    while (true) {
        // Read from buffer non-blockingly
        telemetry_packet_t *item = (telemetry_packet_t *)xRingbufferReceive(telemetry_ringbuf, &item_size, 0);
        if (item == NULL) break; // Buffer empty

        if (!first) {
            httpd_resp_sendstr_chunk(req, ",");
        }
        first = false;

        // Format single item as JSON
        // Format single item as JSON (Updated to include node_id and leaf_temp)
        snprintf(json_buf, sizeof(json_buf), 
                 "{\"timestamp\":%lu,\"node_id\":%lu,\"pressure\":%.2f,\"water_temp\":%.2f,\"light_lux\":%.2f,\"tds_value\":%.2f,\"soil_moisture\":%.2f,\"air_temp\":%.2f,\"humidity\":%.2f,\"leaf_temp\":%.2f}",
                 (unsigned long)item->timestamp, (unsigned long)item->node_id, item->pressure, item->water_temp, item->light_lux, item->tds_value, item->soil_moisture, item->air_temp, item->humidity, item->leaf_temp);        
        // Send chunk to PC
        httpd_resp_sendstr_chunk(req, json_buf);
        
        // Free the memory in the ring buffer
        vRingbufferReturnItem(telemetry_ringbuf, (void *)item);
        items_dumped++;
    }

    httpd_resp_sendstr_chunk(req, "]");
    httpd_resp_sendstr_chunk(req, NULL); // End the HTTP response
    
    ESP_LOGI(TAG, "Successfully transmitted %d records to operator.", items_dumped);
    return ESP_OK;
}

// --- HTTP Server: Set Time Handler ---
esp_err_t set_time_handler(httpd_req_t *req) {
    char buf[32];
    int ret, remaining = req->content_len;

    if (remaining >= sizeof(buf)) {
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }

    ret = httpd_req_recv(req, buf, remaining);
    if (ret <= 0) {
        return ESP_FAIL;
    }
    buf[ret] = '\0';

    // Parse the incoming Unix epoch timestamp
    long int epoch = atol(buf);
    if (epoch > 0) {
        struct timeval tv;
        tv.tv_sec = epoch;
        tv.tv_usec = 0;
        settimeofday(&tv, NULL);
        ESP_LOGI(TAG, "System time synced to epoch: %ld", epoch);
        httpd_resp_sendstr(req, "Time synced");
    } else {
        httpd_resp_send_500(req);
    }
    return ESP_OK;
}

// --- Initialization Functions ---
static void start_webserver() {
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    httpd_handle_t server = NULL;

    if (httpd_start(&server, &config) == ESP_OK) {
        httpd_uri_t uri_dump = {
            .uri      = "/dump",
            .method   = HTTP_GET,
            .handler  = download_data_handler,
            .user_ctx = NULL
        };
        httpd_register_uri_handler(server, &uri_dump);

        httpd_uri_t uri_time = {
            .uri      = "/set_time",
            .method   = HTTP_POST,
            .handler  = set_time_handler,
            .user_ctx = NULL
        };
        httpd_register_uri_handler(server, &uri_time);
        
        ESP_LOGI(TAG, "Web server started on http://192.168.4.1. Endpoints: /dump, /set_time");
    } else {
        ESP_LOGE(TAG, "Failed to start web server!");
    }
}

static void wifi_init_ap() {
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    
    // CRITICAL: Initialize BOTH interfaces
    esp_netif_create_default_wifi_ap();
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    wifi_config_t wifi_config = {};
    strcpy((char *)wifi_config.ap.ssid, "GREENHOUSE_STAR");
    wifi_config.ap.ssid_len = strlen("GREENHOUSE_STAR");
    strcpy((char *)wifi_config.ap.password, "operator123"); 
    wifi_config.ap.max_connection = 4;
    wifi_config.ap.authmode = WIFI_AUTH_WPA2_PSK;
    wifi_config.ap.channel = 1; 

    // CRITICAL: Set to APSTA so ESP-NOW can listen on the STA MAC
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_APSTA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &wifi_config));
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

// --- ESP-NOW Callback ---
void OnDataRecv(const esp_now_recv_info_t *esp_now_info, const uint8_t *incomingData, int len) {
    telemetry_packet_t myData;
    if (len == sizeof(myData)) {
        memcpy(&myData, incomingData, sizeof(myData));
        xQueueSendFromISR(telemetry_queue, &myData, NULL);
    } else {
        ESP_LOGE(TAG, "Packet size mismatch.");
    }
}

// --- Task 1: Data Dispatcher (Core 0) ---
void reception_task(void *pvParameters) {
    ESP_LOGI(TAG, "Reception Task Running on Core %d", xPortGetCoreID());
    telemetry_packet_t incomingData;
    
    while (true) {
        if (xQueueReceive(telemetry_queue, &incomingData, portMAX_DELAY)) {
            // Apply timestamp
            time_t now;
            time(&now);
            incomingData.timestamp = (uint32_t)now;

            // Push to Ring Buffer for Operator Dump
            UBaseType_t res = xRingbufferSend(telemetry_ringbuf, &incomingData, sizeof(telemetry_packet_t), 0);
            if (res != pdTRUE) {
                size_t dummy_size;
                void *old_item = xRingbufferReceive(telemetry_ringbuf, &dummy_size, 0);
                if (old_item) {
                    vRingbufferReturnItem(telemetry_ringbuf, old_item); 
                    xRingbufferSend(telemetry_ringbuf, &incomingData, sizeof(telemetry_packet_t), 0);
                }
            }
            // Send copy to display mailbox
            xQueueOverwrite(display_mailbox, &incomingData);
        }
    }
}

// --- Task 2: Data Manager & Display (Core 1) ---
void data_manager_task(void *pvParameters) {
    ESP_LOGI(TAG, "Display Task Running on Core %d", xPortGetCoreID());
    telemetry_packet_t displayData;
    char buffer[32];
    
    while (true) {
        if (xQueueReceive(display_mailbox, &displayData, portMAX_DELAY)) {
            // Update OLED Display
            u8g2_ClearBuffer(&u8g2);
            sprintf(buffer, "Pres: %.1f Pa", displayData.pressure);
            u8g2_DrawStr(&u8g2, 0, 10, buffer);
            sprintf(buffer, "Water Temp: %.1f C", displayData.water_temp);
            u8g2_DrawStr(&u8g2, 0, 22, buffer);
            sprintf(buffer, "Lux: %.1f TDS: %.0f", displayData.light_lux, displayData.tds_value);
            u8g2_DrawStr(&u8g2, 0, 34, buffer);
            sprintf(buffer, "Soil Moist: %.1f%%", displayData.soil_moisture);
            u8g2_DrawStr(&u8g2, 0, 46, buffer);
            sprintf(buffer, "Air: %.1fC / %.1f%%", displayData.air_temp, displayData.humidity);
            u8g2_DrawStr(&u8g2, 0, 58, buffer);
            u8g2_SendBuffer(&u8g2);

            // BACKGROUND WORK (LoRa etc) goes here.
            // Do NOT touch the ring buffer in this task!
        }
    }
}

// --- Main Application Entry ---
extern "C" void app_main(void)
{
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        nvs_flash_init();
    }

    uint8_t *ringbuf_storage = (uint8_t *)heap_caps_malloc(INTERNAL_BUFFER_SIZE, MALLOC_CAP_INTERNAL);
    StaticRingbuffer_t *ringbuf_struct = (StaticRingbuffer_t *)heap_caps_malloc(sizeof(StaticRingbuffer_t), MALLOC_CAP_8BIT);
    telemetry_ringbuf = xRingbufferCreateStatic(INTERNAL_BUFFER_SIZE, RINGBUF_TYPE_NOSPLIT, ringbuf_storage, ringbuf_struct);
    
    telemetry_queue = xQueueCreate(20, sizeof(telemetry_packet_t));
    display_mailbox = xQueueCreate(1, sizeof(telemetry_packet_t));

    display_init();
    wifi_init_ap(); // <--- Start AP Mode
    start_webserver(); // <--- Start HTTP Server

    if (esp_now_init() != ESP_OK) return;
    esp_now_register_recv_cb(OnDataRecv);

    xTaskCreatePinnedToCore(reception_task, "Telemetry_Recv", 4096, NULL, 5, NULL, 0);
    xTaskCreatePinnedToCore(data_manager_task, "Display_Mgr", 4096, NULL, 4, NULL, 1);

    vTaskDelete(NULL);
}