#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <sys/time.h>
#include <time.h>
#include <map>
#include <array>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/ringbuf.h"
#include "freertos/semphr.h"
#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_now.h"
#include "esp_netif.h"
#include "esp_mac.h"
#include "nvs_flash.h"
#include "esp_heap_caps.h"
#include "esp_http_server.h"
#include "coap3/coap.h"
#include "cJSON.h"
#include <RadioLib.h>
#include "EspHal.h"
#include "TelemetryPacket.h"
#include "CommandPacket.h"
#include "config.hpp"

#ifdef IS_HELTEC
#include "u8g2.h"
extern "C" {
    #include "u8g2_esp32_hal.h"
}
#endif

static const char *TAG = "GREENHOUSE_STAR";

// Wraps an incoming ESP-NOW packet together with the sender's MAC so
// reception_task can later reply with a command over the same link.
typedef struct {
    telemetry_packet_t packet;
    uint8_t sender_mac[6];
} received_espnow_t;

// Global Handles
RingbufHandle_t telemetry_ringbuf = NULL;
QueueHandle_t   telemetry_queue;   // carries received_espnow_t
QueueHandle_t   display_mailbox;   // carries telemetry_packet_t

// CoAP
telemetry_packet_t last_received_data;
coap_resource_t *telemetry_resource = NULL;

// RadioLib
EspHal*  hal   = nullptr;
Module*  mod   = nullptr;
SX1262*  radio = nullptr;

// Star identity (derived from own MAC, same formula as node_id on Nodes)
static uint32_t star_id = 0;

// Mutex protecting SPI access to the LoRa radio (shared between lora_rx_task and reception_task)
static SemaphoreHandle_t lora_mutex = NULL;

// Set by DIO1 interrupt when a LoRa packet arrives; cleared after readData()
static volatile bool lora_rx_flag = false;
void IRAM_ATTR setLoraRxFlag() { lora_rx_flag = true; }

// Pending actuation commands keyed by node_id
static SemaphoreHandle_t pending_cmds_mutex = NULL;
static std::map<uint32_t, command_packet_t> pending_cmds;

// Node MAC addresses, learned from incoming ESP-NOW packets
static std::map<uint32_t, std::array<uint8_t, 6>> node_macs;

#ifdef IS_HELTEC
u8g2_t u8g2;
#endif

// LoRa Initialization
void lora_init() {
    ESP_LOGI(TAG, "Initializing SPI for LoRa...");

    // spi_bus_config_t buscfg = {};
    // buscfg.mosi_io_num   = LORA_MOSI;
    // buscfg.miso_io_num   = LORA_MISO;
    // buscfg.sclk_io_num   = LORA_SCK;
    // buscfg.quadwp_io_num = -1;
    // buscfg.quadhd_io_num = -1;
    // buscfg.max_transfer_sz = 0;

    // // ESP_ERROR_CHECK(spi_bus_initialize(SPI3_HOST, &buscfg, SPI_DMA_CH_AUTO));
    // esp_err_t ret = spi_bus_initialize(SPI3_HOST, &buscfg, SPI_DMA_CH_AUTO);
    // if (ret != ESP_OK && ret != ESP_ERR_INVALID_STATE) {
    //     ESP_LOGE(TAG, "SPI init failed: %s", esp_err_to_name(ret));
    //     return;
    // }

    // // gpio_set_pull_mode((gpio_num_t)LORA_BUSY, GPIO_PULLDOWN_ONLY);

    hal   = new EspHal(LORA_SCK, LORA_MISO, LORA_MOSI, SPI3_HOST, SPI_MASTER_FREQ_8M);
    mod   = new Module(hal, LORA_CS, LORA_DIO1, LORA_RST, LORA_BUSY);
    radio = new SX1262(mod);

    ESP_LOGI(TAG, "Sensing presence of a LoRA module...");
    int state = radio->begin(868.0, 125.0, 9, 7, 0x12, 10, 8, 1.6, false);
    if (state == RADIOLIB_ERR_NONE) {
        ESP_LOGI(TAG, "LoRa initialized successfully!");
        // Ripristina il pull a stato neutrale se il modulo esiste davvero
        gpio_set_pull_mode((gpio_num_t)LORA_BUSY, GPIO_FLOATING); 
    } else {
        ESP_LOGE(TAG, "LoRa module not present or broken, radio obj disabled. Error code: %d", state);
        delete radio; radio = nullptr;
        delete mod;   mod = nullptr;
        delete hal;   hal = nullptr;
        
        
    }

}

// HTTP Server Handlers

esp_err_t download_data_handler(httpd_req_t *req) {
    ESP_LOGI(TAG, "Operator requested data dump via Wi-Fi...");

    httpd_resp_set_type(req, "application/json");
    httpd_resp_sendstr_chunk(req, "[");

    size_t item_size;
    bool first = true;
    int items_dumped = 0;
    char json_buf[256];

    while (true) {
        telemetry_packet_t *item = (telemetry_packet_t *)xRingbufferReceive(telemetry_ringbuf, &item_size, 0);
        if (item == NULL) break;

        if (!first) httpd_resp_sendstr_chunk(req, ",");
        first = false;

        snprintf(json_buf, sizeof(json_buf),
                 "{\"timestamp\":%lu,\"node_id\":%lu,\"pressure\":%.2f,\"water_temp\":%.2f,"
                 "\"light_lux\":%.2f,\"tds_value\":%.2f,\"soil_moisture\":%.2f,"
                 "\"air_temp\":%.2f,\"humidity\":%.2f,\"leaf_temp\":%.2f}",
                 (unsigned long)item->timestamp, (unsigned long)item->node_id,
                 item->pressure, item->water_temp, item->light_lux,
                 item->tds_value, item->soil_moisture, item->air_temp,
                 item->humidity, item->leaf_temp);

        httpd_resp_sendstr_chunk(req, json_buf);
        vRingbufferReturnItem(telemetry_ringbuf, (void *)item);
        items_dumped++;
    }

    httpd_resp_sendstr_chunk(req, "]");
    httpd_resp_sendstr_chunk(req, NULL);

    ESP_LOGI(TAG, "Transmitted %d records to operator.", items_dumped);
    return ESP_OK;
}

esp_err_t set_time_handler(httpd_req_t *req) {
    char buf[32];
    int ret, remaining = req->content_len;

    if (remaining >= (int)sizeof(buf)) {
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }

    ret = httpd_req_recv(req, buf, remaining);
    if (ret <= 0) return ESP_FAIL;
    buf[ret] = '\0';

    long int epoch = atol(buf);
    if (epoch > 0) {
        struct timeval tv = { .tv_sec = epoch, .tv_usec = 0 };
        settimeofday(&tv, NULL);
        ESP_LOGI(TAG, "System time synced to epoch: %ld", epoch);
        httpd_resp_sendstr(req, "Time synced");
    } else {
        httpd_resp_send_500(req);
    }
    return ESP_OK;
}

esp_err_t info_handler(httpd_req_t *req) {
    char buf[32];
    snprintf(buf, sizeof(buf), "{\"star_id\":%lu}", (unsigned long)star_id);
    httpd_resp_set_type(req, "application/json");
    httpd_resp_sendstr(req, buf);
    return ESP_OK;
}

esp_err_t command_handler(httpd_req_t *req) {
    char buf[128];
    int ret, remaining = req->content_len;
    if (remaining >= (int)sizeof(buf)) { httpd_resp_send_500(req); return ESP_FAIL; }
    ret = httpd_req_recv(req, buf, remaining);
    if (ret <= 0) return ESP_FAIL;
    buf[ret] = '\0';

    cJSON *json = cJSON_Parse(buf);
    if (!json) { httpd_resp_send_500(req); return ESP_FAIL; }

    cJSON *nid = cJSON_GetObjectItem(json, "nid");
    cJSON *act = cJSON_GetObjectItem(json, "act");
    cJSON *val = cJSON_GetObjectItem(json, "val");
    cJSON *dur = cJSON_GetObjectItem(json, "dur");

    if (cJSON_IsNumber(nid) && cJSON_IsString(act) && cJSON_IsNumber(val) && cJSON_IsNumber(dur)) {
        command_packet_t cp = {};
        cp.node_id    = (uint32_t)nid->valuedouble;
        strncpy(cp.actuator, act->valuestring, CMD_ACTUATOR_LEN - 1);
        cp.value      = (uint8_t)val->valueint;
        cp.duration_s = (uint16_t)dur->valueint;

        xSemaphoreTake(pending_cmds_mutex, portMAX_DELAY);
        pending_cmds[cp.node_id] = cp;
        xSemaphoreGive(pending_cmds_mutex);

        ESP_LOGI(TAG, "HTTP command queued for node %lu: %s val=%d dur=%ds",
                 (unsigned long)cp.node_id, cp.actuator, cp.value, cp.duration_s);
        httpd_resp_set_type(req, "application/json");
        httpd_resp_sendstr(req, "{\"status\":\"queued\"}");
    } else {
        cJSON_Delete(json);
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }
    cJSON_Delete(json);
    return ESP_OK;
}

static void start_webserver() {
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    httpd_handle_t server = NULL;

    if (httpd_start(&server, &config) == ESP_OK) {
        httpd_uri_t uri_dump = { "/dump",     HTTP_GET,  download_data_handler, NULL };
        httpd_uri_t uri_time = { "/set_time", HTTP_POST, set_time_handler,      NULL };
        httpd_uri_t uri_info = { "/info",     HTTP_GET,  info_handler,          NULL };
        httpd_uri_t uri_cmd  = { "/command",  HTTP_POST, command_handler,       NULL };
        httpd_register_uri_handler(server, &uri_dump);
        httpd_register_uri_handler(server, &uri_time);
        httpd_register_uri_handler(server, &uri_info);
        httpd_register_uri_handler(server, &uri_cmd);
        ESP_LOGI(TAG, "HTTP server ready at http://192.168.4.1  (/dump, /set_time, /info, /command)");
    } else {
        ESP_LOGE(TAG, "Failed to start HTTP server!");
    }
}

// CoAP

static void hnd_get_telemetry(coap_resource_t *resource, coap_session_t *session,
                               const coap_pdu_t *request, const coap_string_t *query,
                               coap_pdu_t *response) {
    coap_pdu_set_code(response, COAP_RESPONSE_CODE_CONTENT);
    coap_add_data_blocked_response(request, response,
                                   COAP_MEDIATYPE_APPLICATION_OCTET_STREAM, -1,
                                   sizeof(telemetry_packet_t),
                                   (const uint8_t *)&last_received_data);
}

static void coap_server_task(void *p) {
    coap_context_t  *ctx = NULL;
    coap_address_t   serv_addr;

    coap_startup();
    coap_address_init(&serv_addr);
    serv_addr.addr.sin.sin_family = AF_INET;
    serv_addr.addr.sin.sin_port   = htons(5683);

    ctx = coap_new_context(NULL);
    if (!ctx) {
        ESP_LOGE(TAG, "Failed to create CoAP context");
        vTaskDelete(NULL);
    }
    coap_new_endpoint(ctx, &serv_addr, COAP_PROTO_UDP);

    telemetry_resource = coap_resource_init(coap_make_str_const("telemetry"), 0);
    coap_resource_set_get_observable(telemetry_resource, 1);
    coap_register_handler(telemetry_resource, COAP_REQUEST_GET, hnd_get_telemetry);
    coap_add_resource(ctx, telemetry_resource);

    ESP_LOGI(TAG, "CoAP server listening on port 5683");

    while (1) coap_io_process(ctx, 1000);

    coap_free_context(ctx);
    coap_cleanup();
    vTaskDelete(NULL);
}

// Wi-Fi + ESP-NOW

static void wifi_init_ap() {
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    esp_netif_create_default_wifi_ap();
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    wifi_config_t wifi_config = {};
    strcpy((char *)wifi_config.ap.ssid, "GREENHOUSE_STAR");
    wifi_config.ap.ssid_len      = strlen("GREENHOUSE_STAR");
    strcpy((char *)wifi_config.ap.password, "operator123");
    wifi_config.ap.max_connection = 4;
    wifi_config.ap.authmode       = WIFI_AUTH_WPA2_PSK;
    wifi_config.ap.channel        = 1;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_APSTA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());
}

#ifdef IS_HELTEC
static void display_init() {
    gpio_set_direction((gpio_num_t)VEXT_PIN, GPIO_MODE_OUTPUT);
    gpio_set_level((gpio_num_t)VEXT_PIN, 0);
    vTaskDelay(pdMS_TO_TICKS(50));

    u8g2_esp32_hal_t u8g2_esp32_hal = U8G2_ESP32_HAL_DEFAULT;
    u8g2_esp32_hal.bus.i2c.sda = (gpio_num_t)OLED_SDA;
    u8g2_esp32_hal.bus.i2c.scl = (gpio_num_t)OLED_SCL;
    u8g2_esp32_hal.reset        = (gpio_num_t)OLED_RST;
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
#endif

// ESP-NOW receive callback — called from ISR context.
// Saves the sender MAC alongside the packet so reception_task can reply.
void OnDataRecv(const esp_now_recv_info_t *esp_now_info, const uint8_t *incomingData, int len) {
    received_espnow_t item;
    if (len == sizeof(telemetry_packet_t)) {
        memcpy(&item.packet,     incomingData,         sizeof(telemetry_packet_t));
        memcpy(&item.sender_mac, esp_now_info->src_addr, 6);
        xQueueSendFromISR(telemetry_queue, &item, NULL);
    } else {
        ESP_LOGE(TAG, "Packet size mismatch: got %d, expected %d", len, (int)sizeof(telemetry_packet_t));
    }
}

// Task: listen for LoRa downlink command frames from the Gateway.
// Uses interrupt-driven continuous receive so the radio stays in RX at all times.
// The mutex is held only for the brief readData() + startReceive() calls.
static void lora_rx_task(void *p) {
    if (radio == nullptr) {
        ESP_LOGW(TAG, "Modulo LoRa disabilitato. Terminazione preventiva del task lora_rx_task.");
        vTaskDelete(NULL);
        return; 
    }
    uint8_t buf[256];
    ESP_LOGI(TAG, "LoRa RX task running on Core %d", xPortGetCoreID());

    xSemaphoreTake(lora_mutex, portMAX_DELAY);
    radio->setDio1Action(setLoraRxFlag);
    radio->startReceive();
    xSemaphoreGive(lora_mutex);

    while (1) {
        if (lora_rx_flag) {
            if (xSemaphoreTake(lora_mutex, portMAX_DELAY) == pdTRUE) {
                lora_rx_flag = false;
                memset(buf, 0, sizeof(buf));
                int state = radio->readData(buf, sizeof(buf) - 1);
                // Do not re-arm yet: valid commands need to TX an ACK first
                xSemaphoreGive(lora_mutex);

                if (state == RADIOLIB_ERR_NONE) {
                    ESP_LOGI(TAG, "LoRa RX command: %s", (char *)buf);

                    bool valid = false;
                    uint32_t acked_nid = 0;
                    cJSON *json = cJSON_Parse((char *)buf);
                    if (json) {
                        cJSON *nid = cJSON_GetObjectItem(json, "nid");
                        cJSON *act = cJSON_GetObjectItem(json, "act");
                        cJSON *val = cJSON_GetObjectItem(json, "val");
                        cJSON *dur = cJSON_GetObjectItem(json, "dur");

                        if (cJSON_IsNumber(nid) && cJSON_IsString(act)
                            && cJSON_IsNumber(val) && cJSON_IsNumber(dur)) {

                            command_packet_t cp = {};
                            cp.node_id    = (uint32_t)nid->valuedouble;
                            strncpy(cp.actuator, act->valuestring, CMD_ACTUATOR_LEN - 1);
                            cp.value      = (uint8_t)val->valueint;
                            cp.duration_s = (uint16_t)dur->valueint;

                            xSemaphoreTake(pending_cmds_mutex, portMAX_DELAY);
                            pending_cmds[cp.node_id] = cp;
                            xSemaphoreGive(pending_cmds_mutex);

                            ESP_LOGI(TAG, "Command queued for node %lu: %s val=%d dur=%ds",
                                     (unsigned long)cp.node_id, cp.actuator, cp.value, cp.duration_s);
                            acked_nid = cp.node_id;
                            valid = true;
                        }
                        cJSON_Delete(json);
                    }

                    if (valid) {
                        // ACK before re-arming RX; include nid so the backend can correlate
                        char ack[32];
                        snprintf(ack, sizeof(ack), "{\"ack\":1,\"nid\":%lu}", (unsigned long)acked_nid);
                        // The gateway flips TX->RX exactly when this command finishes on air;
                        // its receiver hasn't settled yet. Wait so the ACK preamble arrives
                        // after the gateway is fully listening (LoRaWAN-style RX window).
                        vTaskDelay(pdMS_TO_TICKS(50));
                        xSemaphoreTake(lora_mutex, portMAX_DELAY);
                        radio->transmit((uint8_t *)ack, strlen(ack));
                        lora_rx_flag = false;
                        radio->startReceive();
                        xSemaphoreGive(lora_mutex);
                        ESP_LOGI(TAG, "ACK sent");
                    } else {
                        xSemaphoreTake(lora_mutex, portMAX_DELAY);
                        radio->startReceive();
                        xSemaphoreGive(lora_mutex);
                    }
                } else {
                    ESP_LOGW(TAG, "LoRa readData error: %d", state);
                    xSemaphoreTake(lora_mutex, portMAX_DELAY);
                    radio->startReceive();
                    xSemaphoreGive(lora_mutex);
                }
            }
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

// Task: receive telemetry from Nodes, relay upstream, deliver pending commands.
void reception_task(void *pvParameters) {
    ESP_LOGI(TAG, "Reception Task running on Core %d", xPortGetCoreID());
    received_espnow_t incoming;

    while (1) {
        if (xQueueReceive(telemetry_queue, &incoming, portMAX_DELAY)) {
            telemetry_packet_t &pkt = incoming.packet;

            time_t now;
            time(&now);
            pkt.timestamp = (uint32_t)now;

            // Learn this node's MAC so we can reply later
            std::array<uint8_t, 6> mac;
            memcpy(mac.data(), incoming.sender_mac, 6);
            node_macs[pkt.node_id] = mac;

            if (!esp_now_is_peer_exist(incoming.sender_mac)) {
                esp_now_peer_info_t peer = {};
                memcpy(peer.peer_addr, incoming.sender_mac, 6);
                peer.channel = 0;
                peer.encrypt = false;
                esp_now_add_peer(&peer);
            }

            // Serialize and TX via LoRa (abbreviated keys + star_id)
            char lora_payload[256];
            snprintf(lora_payload, sizeof(lora_payload),
                     "{\"sid\":%lu,\"ts\":%lu,\"id\":%lu,\"p\":%.2f,\"wt\":%.2f,"
                     "\"lux\":%.2f,\"tds\":%.2f,\"sm\":%.2f,\"at\":%.2f,\"h\":%.2f,\"lt\":%.2f}",
                     (unsigned long)star_id,
                     (unsigned long)pkt.timestamp, (unsigned long)pkt.node_id,
                     pkt.pressure, pkt.water_temp, pkt.light_lux,
                     pkt.tds_value, pkt.soil_moisture, pkt.air_temp,
                     pkt.humidity, pkt.leaf_temp);

            if (radio != nullptr) {
                xSemaphoreTake(lora_mutex, portMAX_DELAY);
                int state = radio->transmit(lora_payload);
                lora_rx_flag = false;       // discard any spurious flag raised during TX
                radio->startReceive();      // re-arm continuous RX after TX
                xSemaphoreGive(lora_mutex);
                if (state == RADIOLIB_ERR_NONE) {
                    ESP_LOGI(TAG, "LoRa TX OK: %s", lora_payload);
                } else {
                    ESP_LOGW(TAG, "LoRa TX failed, code: %d", state);
                }
            }

            // Update CoAP observable resource
            memcpy(&last_received_data, &pkt, sizeof(telemetry_packet_t));
            if (telemetry_resource != NULL) {
                coap_resource_notify_observers(telemetry_resource, NULL);
            }

            // Push to ring buffer for HTTP /dump
            if (xRingbufferSend(telemetry_ringbuf, &pkt, sizeof(telemetry_packet_t), 0) != pdTRUE) {
                size_t dummy;
                void *old = xRingbufferReceive(telemetry_ringbuf, &dummy, 0);
                if (old) {
                    vRingbufferReturnItem(telemetry_ringbuf, old);
                    xRingbufferSend(telemetry_ringbuf, &pkt, sizeof(telemetry_packet_t), 0);
                }
            }

            // Update display
            xQueueOverwrite(display_mailbox, &pkt);

            // Deliver a pending command to this node if one is queued
            xSemaphoreTake(pending_cmds_mutex, portMAX_DELAY);
            auto it = pending_cmds.find(pkt.node_id);
            if (it != pending_cmds.end()) {
                command_packet_t cmd = it->second;
                pending_cmds.erase(it);
                xSemaphoreGive(pending_cmds_mutex);

                auto mac_it = node_macs.find(pkt.node_id);
                if (mac_it != node_macs.end()) {
                    esp_err_t err = esp_now_send(mac_it->second.data(), (uint8_t *)&cmd, sizeof(cmd));
                    if (err == ESP_OK) {
                        ESP_LOGI(TAG, "Command dispatched to node %lu: %s val=%d dur=%ds",
                                 (unsigned long)cmd.node_id, cmd.actuator, cmd.value, cmd.duration_s);
                    } else {
                        ESP_LOGE(TAG, "Failed to send command to node %lu: %s",
                                 (unsigned long)cmd.node_id, esp_err_to_name(err));
                    }
                }
            } else {
                xSemaphoreGive(pending_cmds_mutex);
            }
        }
    }
}

// Task: update OLED (Heltec) or log data (generic board)
void data_manager_task(void *pvParameters) {
    ESP_LOGI(TAG, "Display Task running on Core %d", xPortGetCoreID());
    telemetry_packet_t displayData;

    #ifdef IS_HELTEC
    char buffer[32];
    #endif

    while (true) {
        if (xQueueReceive(display_mailbox, &displayData, portMAX_DELAY)) {
            #ifdef IS_HELTEC
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
            #else
            ESP_LOGI(TAG, "Data: Pres=%.1fPa Water=%.1fC Lux=%.1f TDS=%.0f Soil=%.1f%% Air=%.1fC/%.1f%%",
                     displayData.pressure, displayData.water_temp, displayData.light_lux,
                     displayData.tds_value, displayData.soil_moisture, displayData.air_temp,
                     displayData.humidity);
            #endif
        }
    }
}

extern "C" void app_main(void) {
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        nvs_flash_init();
    }

    // Allocate ring buffer
    uint8_t *ringbuf_storage = (uint8_t *)heap_caps_malloc(INTERNAL_BUFFER_SIZE, MALLOC_CAP_INTERNAL);
    StaticRingbuffer_t *ringbuf_struct = (StaticRingbuffer_t *)heap_caps_malloc(sizeof(StaticRingbuffer_t), MALLOC_CAP_8BIT);
    telemetry_ringbuf = xRingbufferCreateStatic(INTERNAL_BUFFER_SIZE, RINGBUF_TYPE_NOSPLIT, ringbuf_storage, ringbuf_struct);

    telemetry_queue  = xQueueCreate(20, sizeof(received_espnow_t));
    display_mailbox  = xQueueCreate(1,  sizeof(telemetry_packet_t));
    lora_mutex       = xSemaphoreCreateMutex();
    pending_cmds_mutex = xSemaphoreCreateMutex();

    #ifdef IS_HELTEC
    display_init();
    #endif

    wifi_init_ap();
    start_webserver();
    lora_init();

    // Derive star_id from own MAC (last 4 bytes, same formula as node_id on Nodes)
    uint8_t mac[6];
    ret = esp_read_mac(mac, ESP_MAC_WIFI_STA);
    if (ret == ESP_OK) {
        ESP_LOGI(TAG, "MAC (Wi-Fi STA): %02X:%02X:%02X:%02X:%02X:%02X",
                 mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
        star_id = ((uint32_t)mac[2] << 24) | ((uint32_t)mac[3] << 16) |
                  ((uint32_t)mac[4] << 8)  |  (uint32_t)mac[5];
        ESP_LOGI(TAG, "Star ID: %lu", (unsigned long)star_id);
    }

    if (esp_now_init() != ESP_OK) {
        ESP_LOGE(TAG, "ESP-NOW init failed");
        return;
    }
    esp_now_register_recv_cb(OnDataRecv);

    xTaskCreatePinnedToCore(coap_server_task,  "coap_server",   8192, NULL, 5, NULL, 0);

    if (radio != nullptr) {
        xTaskCreatePinnedToCore(lora_rx_task,  "LoRa_RX",       4096, NULL, 4, NULL, 0);
    }

    xTaskCreatePinnedToCore(reception_task,    "Telemetry_Recv",4096, NULL, 5, NULL, 0);
    xTaskCreatePinnedToCore(data_manager_task, "Display_Mgr",   4096, NULL, 4, NULL, 1);
}
