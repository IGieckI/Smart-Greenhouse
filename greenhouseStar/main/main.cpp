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


RingbufHandle_t telemetry_ringbuf = NULL;

// carries received_espnow_t
QueueHandle_t   telemetry_queue;

// carries telemetry_packet_t
QueueHandle_t   display_mailbox;

// CoAP
telemetry_packet_t last_received_data;
coap_resource_t *telemetry_resource = NULL;

// Raised by reception_task when new telemetry is ready. The observe
// notification itself is issued by coap_server_task, because this libcoap build
// is not thread-safe: the CoAP context must only be
// touched from the task that runs coap_io_process.
static volatile bool telemetry_dirty = false;

// RadioLib
EspHal*  hal   = nullptr;
Module*  mod   = nullptr;
SX1262*  radio = nullptr;

static uint32_t star_id = 0;

// Mutex protecting SPI access to the LoRa radio (shared between lora_rx_task and reception_task)
static SemaphoreHandle_t lora_mutex = NULL;

// lora_rx_task blocks on a task notification. The DIO1 ISR notifies it on RX-done
static TaskHandle_t lora_rx_task_handle = NULL;
void IRAM_ATTR setLoraRxFlag() {
    BaseType_t hpw = pdFALSE;
    vTaskNotifyGiveFromISR(lora_rx_task_handle, &hpw);
    portYIELD_FROM_ISR(hpw);
}

// Set once the system clock has been synced
static volatile bool time_synced = false;

// Discard a stray TX-done notification on lora_rx_task before re-arming RX, so a
// completed transmit is never mistaken for a received packet. Call while holding
// lora_mutex, between transmit() and startReceive().
static inline void lora_notify_clear() {
    if (lora_rx_task_handle) ulTaskNotifyValueClear(lora_rx_task_handle, 0xFFFFFFFFUL);
}

// Pending actuation commands keyed by node_id
static SemaphoreHandle_t pending_cmds_mutex = NULL;
static std::map<uint32_t, command_packet_t> pending_cmds;

// Node MAC addresses, learned from incoming ESP-NOW packets
static std::map<uint32_t, std::array<uint8_t, 6>> node_macs;

#ifdef IS_HELTEC
u8g2_t u8g2;
#endif

/**
 * LoRa Initialization (based on EspHal and RadioLib)
 * Note: Radio init values are taken from Semtech's SX1262 datasheet
 */
void lora_init() {
    ESP_LOGI(TAG, "Initializing SPI for LoRa...");

    hal   = new EspHal(LORA_SCK, LORA_MISO, LORA_MOSI, SPI3_HOST, SPI_MASTER_FREQ_8M);
    mod   = new Module(hal, LORA_CS, LORA_DIO1, LORA_RST, LORA_BUSY);
    radio = new SX1262(mod);

    ESP_LOGI(TAG, "Sensing presence of a LoRA module...");

    int state = radio->begin(868.0, 125.0, 9, 7, 0x12, 10, 8, 1.6, false);

    if (state == RADIOLIB_ERR_NONE) {
        ESP_LOGI(TAG, "LoRa initialized successfully!");
        gpio_set_pull_mode((gpio_num_t)LORA_BUSY, GPIO_FLOATING); 
    } else {
        ESP_LOGE(TAG, "LoRa module not present or broken, radio obj disabled. Error code: %d", state);
        delete radio; radio = nullptr;
        delete mod;   mod = nullptr;
        delete hal;   hal = nullptr;
    }
}

/**
 * CoAP GET handler for /info, returns the star_id as JSON + GET /info?ts=<epoch>
 * to set the system clock.
 */
static void hnd_get_info(coap_resource_t *resource, coap_session_t *session,
                         const coap_pdu_t *request, const coap_string_t *query,
                         coap_pdu_t *response) {
    if (query && query->length > 0) {
        char qbuf[64];
        size_t qlen = query->length < sizeof(qbuf) - 1 ? query->length : sizeof(qbuf) - 1;
        memcpy(qbuf, query->s, qlen);
        qbuf[qlen] = '\0';
        char *p = strstr(qbuf, "ts=");
        if (p && (p == qbuf || p[-1] == '&')) {
            long int epoch = atol(p + 3);
            if (epoch > 0) {
                struct timeval tv = { .tv_sec = epoch, .tv_usec = 0 };
                settimeofday(&tv, NULL);
                time_synced = true;
                ESP_LOGI(TAG, "System time synced via /info?ts=%ld", epoch);
            }
        }
    }

    char buf[32];
    int n = snprintf(buf, sizeof(buf), "{\"star_id\":%lu}", (unsigned long)star_id);
    coap_pdu_set_code(response, COAP_RESPONSE_CODE_CONTENT);
    coap_add_data_blocked_response(request, response,
                                   COAP_MEDIATYPE_APPLICATION_JSON, 0,
                                   (size_t)n, (const uint8_t *)buf);
}

/**
 * Frees the heap snapshot handed to coap_add_data_large_response once libcoap
 * has finished transmitting every block of a /dump transfer
 */
static void free_dump_buf(coap_session_t *session, void *app_ptr) {
    (void)session;
    free(app_ptr);
}

/**
 * CoAP GET handler for /dump. Drains the telemetry ring buffer once into a heap
 * snapshot of packed telemetry_packet_t records and hands it to libcoap, which
 * serves it (in multiple blocks if it exceeds one block) without
 * re-invoking this handler, so the destructive drain happens exactly once per
 * transfer
 */
static void hnd_get_dump(coap_resource_t *resource, coap_session_t *session,
                         const coap_pdu_t *request, const coap_string_t *query,
                         coap_pdu_t *response) {
    ESP_LOGI(TAG, "Operator requested data dump via CoAP...");

    uint8_t *buf = (uint8_t *)malloc(INTERNAL_BUFFER_SIZE);
    size_t used = 0;
    if (buf != NULL) {
        size_t item_size;
        while (used + sizeof(telemetry_packet_t) <= INTERNAL_BUFFER_SIZE) {
            telemetry_packet_t *item =
                (telemetry_packet_t *)xRingbufferReceive(telemetry_ringbuf, &item_size, 0);
            if (item == NULL) break;
            if (item_size == sizeof(telemetry_packet_t)) {
                memcpy(buf + used, item, sizeof(telemetry_packet_t));
                used += sizeof(telemetry_packet_t);
            }
            vRingbufferReturnItem(telemetry_ringbuf, item);
        }
    }

    coap_pdu_set_code(response, COAP_RESPONSE_CODE_CONTENT);
    if (used == 0) {
        free(buf);
    } else {
        coap_add_data_large_response(resource, session, request, response, query,
                                     COAP_MEDIATYPE_APPLICATION_OCTET_STREAM,
                                     -1, 0, used, buf, free_dump_buf, buf);
    }

    ESP_LOGI(TAG, "Dumped %u records (%u bytes) to operator.",
             (unsigned)(used / sizeof(telemetry_packet_t)), (unsigned)used);
}

/**
 * CoAP POST handler for /set_time, sets the system clock from an epoch timestamp
 * supplied as an ASCII string in the request payload
 */
static void hnd_post_settime(coap_resource_t *resource, coap_session_t *session,
                             const coap_pdu_t *request, const coap_string_t *query,
                             coap_pdu_t *response) {
    size_t size;
    const uint8_t *data;
    char buf[32];

    if (coap_get_data(request, &size, &data) && size > 0 && size < sizeof(buf)) {
        memcpy(buf, data, size);
        buf[size] = '\0';
        long int epoch = atol(buf);
        if (epoch > 0) {
            struct timeval tv = { .tv_sec = epoch, .tv_usec = 0 };
            settimeofday(&tv, NULL);
            ESP_LOGI(TAG, "System time synced to epoch: %ld", epoch);
            coap_pdu_set_code(response, COAP_RESPONSE_CODE_CHANGED);
            return;
        }
    }
    coap_pdu_set_code(response, COAP_RESPONSE_CODE_BAD_REQUEST);
}

/**
 * CoAP POST handler for /command, queues an actuation command for a node
 */
static void hnd_post_command(coap_resource_t *resource, coap_session_t *session,
                             const coap_pdu_t *request, const coap_string_t *query,
                             coap_pdu_t *response) {
    size_t size;
    const uint8_t *data;
    char buf[128];

    if (!coap_get_data(request, &size, &data) || size == 0 || size >= sizeof(buf)) {
        coap_pdu_set_code(response, COAP_RESPONSE_CODE_BAD_REQUEST);
        return;
    }
    memcpy(buf, data, size);
    buf[size] = '\0';

    cJSON *json = cJSON_Parse(buf);
    if (!json) {
        coap_pdu_set_code(response, COAP_RESPONSE_CODE_BAD_REQUEST);
        return;
    }

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

        ESP_LOGI(TAG, "CoAP command queued for node %lu: %s val=%d dur=%ds",
                 (unsigned long)cp.node_id, cp.actuator, cp.value, cp.duration_s);
        cJSON_Delete(json);

        const char *ok = "{\"status\":\"queued\"}";
        coap_pdu_set_code(response, COAP_RESPONSE_CODE_CHANGED);
        coap_add_data(response, strlen(ok), (const uint8_t *)ok);
        return;
    }

    cJSON_Delete(json);
    coap_pdu_set_code(response, COAP_RESPONSE_CODE_BAD_REQUEST);
}

/**
 * CoAP GET handler for /telemetry resource, returns the last received telemetry packet in binary format
 */
static void hnd_get_telemetry(coap_resource_t *resource, coap_session_t *session,
                               const coap_pdu_t *request, const coap_string_t *query,
                               coap_pdu_t *response) {
    coap_pdu_set_code(response, COAP_RESPONSE_CODE_CONTENT);
    coap_add_data_blocked_response(request, response,
                                   COAP_MEDIATYPE_APPLICATION_OCTET_STREAM, -1,
                                   sizeof(telemetry_packet_t),
                                   (const uint8_t *)&last_received_data);
}

/**
 * CoAP server task, runs in its own FreeRTOS task and handles incoming CoAP requests
 */
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

    coap_context_set_block_mode(ctx, COAP_BLOCK_USE_LIBCOAP | COAP_BLOCK_SINGLE_BODY);

    telemetry_resource = coap_resource_init(coap_make_str_const("telemetry"), 0);
    coap_resource_set_get_observable(telemetry_resource, 1);
    coap_register_handler(telemetry_resource, COAP_REQUEST_GET, hnd_get_telemetry);
    coap_add_resource(ctx, telemetry_resource);

    coap_resource_t *info_res = coap_resource_init(coap_make_str_const("info"), 0);
    coap_register_handler(info_res, COAP_REQUEST_GET, hnd_get_info);
    coap_add_resource(ctx, info_res);

    coap_resource_t *dump_res = coap_resource_init(coap_make_str_const("dump"), 0);
    coap_register_handler(dump_res, COAP_REQUEST_GET, hnd_get_dump);
    coap_add_resource(ctx, dump_res);

    coap_resource_t *time_res = coap_resource_init(coap_make_str_const("set_time"), 0);
    coap_register_handler(time_res, COAP_REQUEST_POST, hnd_post_settime);
    coap_add_resource(ctx, time_res);

    coap_resource_t *cmd_res = coap_resource_init(coap_make_str_const("command"), 0);
    coap_register_handler(cmd_res, COAP_REQUEST_POST, hnd_post_command);
    coap_add_resource(ctx, cmd_res);

    ESP_LOGI(TAG, "CoAP server listening on port 5683 (/telemetry, /info, /dump, /set_time, /command)");

    while (1) {
        if (telemetry_dirty) {
            telemetry_dirty = false;
            coap_resource_notify_observers(telemetry_resource, NULL);
        }
        coap_io_process(ctx, 1000);
    }

    coap_free_context(ctx);
    coap_cleanup();
    vTaskDelete(NULL);
}

/**
 * Wi-Fi initialization in AP+STA mode, sets up an access point for operator connection and a station for upstream connectivity
 */
static void wifi_init_ap() {
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    esp_netif_create_default_wifi_ap();
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    wifi_config_t wifi_config = {};
    strcpy((char *)wifi_config.ap.ssid, WIFI_AP_SSID);
    wifi_config.ap.ssid_len      = strlen(WIFI_AP_SSID);
    strcpy((char *)wifi_config.ap.password, WIFI_AP_PASSWORD);
    wifi_config.ap.max_connection = 4;
    wifi_config.ap.authmode       = WIFI_AUTH_WPA2_PSK;
    wifi_config.ap.channel        = 1;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_APSTA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());
}

#ifdef IS_HELTEC
/**
 * Initializes the OLED display using u8g2 library and sets up I2C communication
 */
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

/**
 * ESP-NOW receive callback, called from ISR context. Saves the sender MAC alongside the packet so reception_task can reply
 */
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

/**
 * LoRa RX task, listens for downlink command frames from the Gateway and uses interrupt-driven continuous 
 * receive so the radio stays in RX at all times. The mutex is held only for the brief readData() + startReceive() calls
 */
static void lora_rx_task(void *p) {
    if (radio == nullptr) {
        ESP_LOGW(TAG, "LoRa module not initialized.");
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
        if (ulTaskNotifyTake(pdTRUE, portMAX_DELAY) == 0) continue;

        if (xSemaphoreTake(lora_mutex, portMAX_DELAY) != pdTRUE) continue;
        memset(buf, 0, sizeof(buf));
        int state = radio->readData(buf, sizeof(buf) - 1);
        xSemaphoreGive(lora_mutex);

        if (state != RADIOLIB_ERR_NONE) {
            ESP_LOGW(TAG, "LoRa readData error: %d", state);
            xSemaphoreTake(lora_mutex, portMAX_DELAY);
            radio->startReceive();
            xSemaphoreGive(lora_mutex);
            continue;
        }

        ESP_LOGI(TAG, "LoRa RX frame: %s", (char *)buf);
        cJSON *json = cJSON_Parse((char *)buf);
        if (!json) {
            xSemaphoreTake(lora_mutex, portMAX_DELAY);
            radio->startReceive();
            xSemaphoreGive(lora_mutex);
            continue;
        }

        cJSON *ts  = cJSON_GetObjectItem(json, "ts");
        cJSON *sid = cJSON_GetObjectItem(json, "sid");
        if (cJSON_IsNumber(ts) && cJSON_IsNumber(sid)
            && (uint32_t)sid->valuedouble == star_id) {
            long int epoch = (long int)ts->valuedouble;
            if (epoch > 0) {
                struct timeval tv = { .tv_sec = epoch, .tv_usec = 0 };
                settimeofday(&tv, NULL);
                time_synced = true;
                ESP_LOGI(TAG, "System time synced via LoRa backend ACK: %ld", epoch);
            }
            cJSON_Delete(json);
            xSemaphoreTake(lora_mutex, portMAX_DELAY);
            radio->startReceive();
            xSemaphoreGive(lora_mutex);
            continue;
        }

        cJSON *nid = cJSON_GetObjectItem(json, "nid");
        cJSON *act = cJSON_GetObjectItem(json, "act");
        cJSON *val = cJSON_GetObjectItem(json, "val");
        cJSON *dur = cJSON_GetObjectItem(json, "dur");

        bool valid = false;
        uint32_t acked_nid = 0;
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

        if (valid) {
            char ack[32];
            snprintf(ack, sizeof(ack), "{\"ack\":1,\"nid\":%lu}", (unsigned long)acked_nid);
            vTaskDelay(pdMS_TO_TICKS(50));
            xSemaphoreTake(lora_mutex, portMAX_DELAY);
            radio->transmit((uint8_t *)ack, strlen(ack));
            lora_notify_clear();
            radio->startReceive();
            xSemaphoreGive(lora_mutex);
            ESP_LOGI(TAG, "ACK sent");
        } else {
            xSemaphoreTake(lora_mutex, portMAX_DELAY);
            radio->startReceive();
            xSemaphoreGive(lora_mutex);
        }
    }
}

/**
 * Tries to synch the board time through LoRa or CoAP, stops when it manages to.
 */
static void time_sync_task(void *p) {
    if (radio == nullptr) { vTaskDelete(NULL); return; }
    ESP_LOGI(TAG, "Time-sync task running on Core %d", xPortGetCoreID());

    char hello[64];
    int hello_len = snprintf(hello, sizeof(hello),
                             "{\"sid\":%lu,\"sync\":1}", (unsigned long)star_id);
    TickType_t backoff = pdMS_TO_TICKS(5000);
    const TickType_t backoff_max = pdMS_TO_TICKS(60000);

    while (!time_synced) {
        xSemaphoreTake(lora_mutex, portMAX_DELAY);
        int state = radio->transmit((uint8_t *)hello, hello_len);
        lora_notify_clear();
        radio->startReceive();
        xSemaphoreGive(lora_mutex);

        if (state == RADIOLIB_ERR_NONE)
            ESP_LOGI(TAG, "Sync hello sent, awaiting backend timestamp...");
        else
            ESP_LOGW(TAG, "Sync hello TX failed, code: %d", state);

        for (TickType_t waited = 0; waited < backoff && !time_synced;
             waited += pdMS_TO_TICKS(500))
            vTaskDelay(pdMS_TO_TICKS(500));

        if (backoff < backoff_max) backoff *= 2;
    }

    ESP_LOGI(TAG, "Clock synced, time_sync_task exiting.");
    vTaskDelete(NULL);
}

/**
 * Receives telemetry packets from ESP-NOW, timestamps them, learns the node's MAC for future replies, forwards the data to LoRa, updates the CoAP observable resource, 
 */
void reception_task(void *pvParameters) {
    ESP_LOGI(TAG, "Reception Task running on Core %d", xPortGetCoreID());
    received_espnow_t incoming;

    while (1) {
        if (xQueueReceive(telemetry_queue, &incoming, portMAX_DELAY)) {
            telemetry_packet_t &pkt = incoming.packet;

            time_t now;
            time(&now);
            pkt.timestamp = (uint32_t)now;

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
                lora_notify_clear();
                radio->startReceive();
                xSemaphoreGive(lora_mutex);
                if (state == RADIOLIB_ERR_NONE) {
                    ESP_LOGI(TAG, "LoRa TX OK: %s", lora_payload);
                } else {
                    ESP_LOGW(TAG, "LoRa TX failed, code: %d", state);
                }
            }

            memcpy(&last_received_data, &pkt, sizeof(telemetry_packet_t));
            telemetry_dirty = true;

            if (xRingbufferSend(telemetry_ringbuf, &pkt, sizeof(telemetry_packet_t), 0) != pdTRUE) {
                size_t dummy;
                void *old = xRingbufferReceive(telemetry_ringbuf, &dummy, 0);
                if (old) {
                    vRingbufferReturnItem(telemetry_ringbuf, old);
                    xRingbufferSend(telemetry_ringbuf, &pkt, sizeof(telemetry_packet_t), 0);
                }
            }

            xQueueOverwrite(display_mailbox, &pkt);

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

/**
 * Data manager task, runs in its own FreeRTOS task and receives telemetry packets from the display mailbox 
 * and updates the OLED display (if present) or logs the data to the console
 */
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
    lora_init();

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

    xTaskCreatePinnedToCore(coap_server_task,  "coap_server",   8192, NULL, 5, NULL, 1);

    if (radio != nullptr) {
        xTaskCreatePinnedToCore(lora_rx_task,  "LoRa_RX",       4096, NULL, 4, &lora_rx_task_handle, 0);
        xTaskCreatePinnedToCore(time_sync_task, "Time_Sync",    4096, NULL, 3, NULL, 1);
    }

    xTaskCreatePinnedToCore(reception_task,    "Telemetry_Recv",4096, NULL, 5, NULL, 0);
    xTaskCreatePinnedToCore(data_manager_task, "Display_Mgr",   4096, NULL, 4, NULL, 1);
}
