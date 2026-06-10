#include <stdio.h>
#include <string.h>
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_mac.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "esp_netif.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "coap3/coap.h"

// Includi RadioLib e il suo HAL specifico per ESP32
#include <RadioLib.h>
// #include <hal/Esp32Hal.h>
// #include <hal/Esp32/Esp32Hal.h>
// #include <hal/esp_flash_err.h>
#include "EspHal.h" // Usa le virgolette per i file locali

#include "driver/spi_master.h"
#include "driver/gpio.h"

#define AP_SSID       "IoT_net"
#define AP_PASS       "1qaz2wsx"
#define MAX_STA_CONN  4

static const char *TAG = "HELTEC_V3_GATEWAY";

// --- PIN LORA HELTEC V3 (ESP32-S3 + SX1262) ---
#define LORA_NSS    8
#define LORA_SCK    9
#define LORA_MOSI   10
#define LORA_MISO   11
#define LORA_RST    12
#define LORA_BUSY   13
#define LORA_DIO1   14

// --- CREDENZIALI THE THINGS NETWORK (OTAA) ---
// ! ATTENZIONE: SOSTITUISCI QUESTI VALORI CON QUELLI DELLA TUA CONSOLE TTN !
// Devono essere in formato MSB (Most Significant Byte / Big-Endian)
uint64_t joinEUI = 0x0000000000000000; 
uint64_t devEUI  = 0x70B3D57ED0077F3C;
uint8_t appKey[] = { 0x3E, 0xE8, 0xD4, 0x07, 0x9C, 0x41, 0xA3, 0x93, 0x22, 0xAA, 0x5E, 0xF1, 0xE0, 0x34, 0x68, 0x33 };

// --- DEFINIZIONE CODA PER I DATI ---
typedef struct {
    uint8_t payload[256];
    size_t length;
} lora_msg_t;

QueueHandle_t lora_queue;

// --- HANDLER CoAP (Ricezione Dati) ---
static void coap_post_handler(coap_resource_t *resource,
                              coap_session_t *session,
                              const coap_pdu_t *request,
                              const coap_string_t *query,
                              coap_pdu_t *response) {
    size_t size;
    const uint8_t *data;
    
    if (coap_get_data(request, &size, &data)) {
        ESP_LOGI(TAG, ">>> Ricevuto pacchetto CoAP. Preparazione per invio LoRa...");
        
        // Crea il messaggio e lo copia in modo sicuro
        lora_msg_t msg;
        msg.length = (size < sizeof(msg.payload)) ? size : (sizeof(msg.payload) - 1);
        memcpy(msg.payload, data, msg.length);
        msg.payload[msg.length] = '\0'; // Terminatore per sicurezza

        // Invia il messaggio alla coda (non blocca il server CoAP)
        if (xQueueSend(lora_queue, &msg, pdMS_TO_TICKS(100)) != pdPASS) {
            ESP_LOGE(TAG, "Coda LoRa piena! Messaggio scartato.");
        }
    }

    coap_pdu_set_code(response, COAP_RESPONSE_CODE_CREATED);
}


// // // --- TASK: LORA END NODE ---
// // static void lora_tx_task(void *p) {
// //     ESP_LOGI(TAG, "Inizializzazione modulo LoRa SX1262 (Heltec V3)...");

// //     // Configura il bus SPI di ESP32-S3 per i pin non standard della Heltec V3
// //     // Esp32Hal* hal = new Esp32Hal(SPI2_HOST, LORA_SCK, LORA_MISO, LORA_MOSI);
// //     EspHal* hal = new EspHal(SPI2_HOST, 2000000, LORA_SCK, LORA_MISO, LORA_MOSI);
// //     SX1262 radio = new Module(hal, LORA_NSS, LORA_DIO1, LORA_RST, LORA_BUSY);

// //     int16_t state = radio.begin();
// //     if (state != RADIOLIB_ERR_NONE) {
// //         ESP_LOGE(TAG, "Errore hardware radio, codice: %d", state);
// //         vTaskDelete(NULL);
// //     }

// //     ESP_LOGI(TAG, "Hardware Radio OK! Avvio procedura di JOIN a TTN...");

// //     // Inizializza LoRaWAN per l'Europa (EU868)
// //     LoRaWANNode node(&radio, &EU868);
// //     state = node.beginOTAA(joinEUI, devEUI, appKey, appKey);

// //     if (state == RADIOLIB_ERR_NONE) {
// //         ESP_LOGI(TAG, "JOIN TTN completato con successo!");
// //     } else {
// //         ESP_LOGE(TAG, "Errore JOIN TTN, codice: %d", state);
// //         ESP_LOGW(TAG, "Verifica le credenziali (EUI/Key) e la copertura del Gateway!");
// //     }

// //     lora_msg_t msg;

// //     while (1) {
// //         // Attende all'infinito un messaggio dalla coda CoAP
// //         if (xQueueReceive(lora_queue, &msg, portMAX_DELAY) == pdPASS) {
// //             ESP_LOGI(TAG, "Invio Uplink LoRaWAN: %.*s", (int)msg.length, msg.payload);
            
// //             // Invia il pacchetto sulla FPort 1 come Uplink Unconfirmed (false)
// //             state = node.sendReceive(msg.payload, msg.length, 1, false);
            
// //             if (state == RADIOLIB_ERR_NONE) {
// //                 ESP_LOGI(TAG, "Uplink inviato con successo a TTN!");
// //             } else {
// //                 ESP_LOGE(TAG, "Errore invio Uplink, codice: %d", state);
// //             }
            
// //             // Pausa per rispettare il duty-cycle LoRaWAN (obbligatorio in Europa)
// //             vTaskDelay(pdMS_TO_TICKS(5000));
// //         }
// //     }
// // }
// // --- TASK: LORA END NODE ---
// static void lora_tx_task(void *p) {
//     ESP_LOGI(TAG, "Inizializzazione modulo LoRa SX1262 (Heltec V3)...");

//     EspHal* hal = new EspHal(SPI2_HOST, 2000000, LORA_SCK, LORA_MISO, LORA_MOSI);
//     SX1262 radio = new Module(hal, LORA_NSS, LORA_DIO1, LORA_RST, LORA_BUSY);

//     int16_t state = radio.begin();
//     if (state != RADIOLIB_ERR_NONE) {
//         ESP_LOGE(TAG, "Errore hardware radio, codice: %d", state);
//         vTaskDelete(NULL);
//     }

//     // --- CONFIGURAZIONE CHIAVE HELTEC V3 ---
//     // Indica al chip di usare il suo pin DIO2 per accendere/spegnere l'antenna
//     radio.setDio2AsRfSwitch(true);

//     ESP_LOGI(TAG, "Hardware Radio OK! Avvio procedura di JOIN a TTN...");

//     // Inizializza LoRaWAN per l'Europa (EU868)
//     LoRaWANNode node(&radio, &EU868);
    
//     // --- CICLO DI JOIN SICURO ---
//     // Non andiamo avanti finché il JOIN non ha successo
//     while (true) {
//         // Nota: appKey ripetuta due volte per compatibilità con LoRaWAN 1.0.x (NwkKey e AppKey)
//         state = node.beginOTAA(joinEUI, devEUI, appKey, appKey); 

//         if (state == RADIOLIB_ERR_NONE) {
//             ESP_LOGI(TAG, "✅ JOIN TTN completato con successo!");
//             break; // Usciamo dal ciclo infinito e procediamo
//         } else {
//             ESP_LOGE(TAG, "❌ Errore JOIN TTN, codice: %d", state);
//             ESP_LOGW(TAG, "Nuovo tentativo tra 10 secondi...");
//             vTaskDelay(pdMS_TO_TICKS(10000));
//         }
//     }

//     lora_msg_t msg;

//     while (1) {
//         // Attende all'infinito un messaggio dalla coda CoAP
//         if (xQueueReceive(lora_queue, &msg, portMAX_DELAY) == pdPASS) {
//             ESP_LOGI(TAG, "Invio Uplink LoRaWAN: %.*s", (int)msg.length, msg.payload);
            
//             // Invia il pacchetto sulla FPort 1 come Uplink Unconfirmed (false)
//             state = node.sendReceive(msg.payload, msg.length, 1, false);
            
//             if (state == RADIOLIB_ERR_NONE) {
//                 ESP_LOGI(TAG, "✅ Uplink inviato con successo a TTN!");
//             } else {
//                 ESP_LOGE(TAG, "❌ Errore invio Uplink, codice: %d", state);
//             }
            
//             // Pausa per rispettare il duty-cycle LoRaWAN (obbligatorio in Europa)
//             vTaskDelay(pdMS_TO_TICKS(5000));
//         }
//     }
// }

// --- TASK: LORA END NODE ---
static void lora_tx_task(void *p) {
    ESP_LOGI(TAG, "Inizializzazione modulo LoRa SX1262 (Heltec V3)...");

    EspHal* hal = new EspHal(SPI2_HOST, 2000000, LORA_SCK, LORA_MISO, LORA_MOSI);
    SX1262 radio = new Module(hal, LORA_NSS, LORA_DIO1, LORA_RST, LORA_BUSY);

    int16_t state = radio.begin();
    if (state != RADIOLIB_ERR_NONE) {
        ESP_LOGE(TAG, "Errore hardware radio, codice: %d", state);
        vTaskDelete(NULL);
    }

    // Configurazione specifica per Heltec V3
    radio.setDio2AsRfSwitch(true);

    ESP_LOGI(TAG, "Hardware Radio OK! Avvio procedura di JOIN a TTN...");

    // Inizializza LoRaWAN per l'Europa (EU868)
    LoRaWANNode node(&radio, &EU868);
    
    // 1. CONFIGURA LE CHIAVI (Passando appKey due volte per LoRaWAN 1.0.x)
    state = node.beginOTAA(joinEUI, devEUI, appKey, appKey); 

    if (state != RADIOLIB_ERR_NONE) {
        ESP_LOGE(TAG, "Errore fatale di configurazione chiavi!");
        vTaskDelete(NULL);
    }

    // 2. ESEGUE IL JOIN FISICO VIA RADIO
    while (true) {
        ESP_LOGI(TAG, "Tentativo di connessione radio a The Things Network...");
        
        // È QUESTO IL COMANDO CHE TRASMETTE E RICEVE!
        state = node.activateOTAA(); 

        if (state == RADIOLIB_ERR_NONE) {
            ESP_LOGI(TAG, "✅ JOIN TTN COMPLETATO FISICAMENTE! Rete stabilita.");
            break; // Esce dal ciclo e passa all'ascolto dei dati CoAP
        } else {
            ESP_LOGE(TAG, "❌ Errore durante il JOIN via radio, codice: %d", state);
            ESP_LOGW(TAG, "Verifica: 1. Antenna avvitata? 2. Gateway acceso? 3. Copertura?");
            vTaskDelay(pdMS_TO_TICKS(10000)); // Aspetta 10 secondi prima di riprovare
        }
    }

    lora_msg_t msg;

    while (1) {
        if (xQueueReceive(lora_queue, &msg, portMAX_DELAY) == pdPASS) {
            ESP_LOGI(TAG, "Invio Uplink LoRaWAN: %.*s", (int)msg.length, msg.payload);
            
            // FPort 1, Unconfirmed (false)
            state = node.sendReceive(msg.payload, msg.length, 1, false);
            
            if (state == RADIOLIB_ERR_NONE) {
                ESP_LOGI(TAG, "✅ Uplink inviato con successo a TTN!");
            } else {
                ESP_LOGE(TAG, "❌ Errore invio Uplink, codice: %d", state);
            }
            
            vTaskDelay(pdMS_TO_TICKS(5000));
        }
    }
}



// --- TASK: SERVER CoAP ---
static void coap_server_task(void *p) {
    coap_context_t *ctx = NULL;
    coap_address_t serv_addr;
    coap_resource_t *resource = NULL;

    coap_startup();
    coap_address_init(&serv_addr);
    serv_addr.addr.sin.sin_family = AF_INET;
    serv_addr.addr.sin.sin_addr.s_addr = INADDR_ANY;
    serv_addr.addr.sin.sin_port = htons(5683); 

    ctx = coap_new_context(NULL);
    coap_new_endpoint(ctx, &serv_addr, COAP_PROTO_UDP);
    
    resource = coap_resource_init(coap_make_str_const("dati"), 0);
    coap_register_handler(resource, COAP_REQUEST_POST, coap_post_handler);
    coap_add_resource(ctx, resource);

    ESP_LOGI(TAG, "Gateway CoAP in ascolto...");

    while (1) {
        coap_io_process(ctx, 1000); 
    }
}

// --- EVENT HANDLER WI-FI ---
static void wifi_event_handler(void* arg, esp_event_base_t event_base, int32_t event_id, void* event_data) {
    if (event_id == WIFI_EVENT_AP_STACONNECTED) {
        wifi_event_ap_staconnected_t* event = (wifi_event_ap_staconnected_t*) event_data;
        ESP_LOGI(TAG, "Dispositivo connesso! MAC: " MACSTR, MAC2STR(event->mac));
    }
}

// --- ENTRY POINT ---
extern "C" void app_main(void) {
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
      ESP_ERROR_CHECK(nvs_flash_erase());
      nvs_flash_init();
    }
    
    // Inizializza la coda (max 10 messaggi pendenti)
    lora_queue = xQueueCreate(10, sizeof(lora_msg_t));

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_ap();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, NULL));

    wifi_config_t wifi_config = {}; 
    memcpy(wifi_config.ap.ssid, AP_SSID, strlen(AP_SSID));
    memcpy(wifi_config.ap.password, AP_PASS, strlen(AP_PASS));
    wifi_config.ap.ssid_len = strlen(AP_SSID);
    wifi_config.ap.channel = 1;
    wifi_config.ap.authmode = WIFI_AUTH_WPA2_PSK;
    wifi_config.ap.max_connection = MAX_STA_CONN;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_LOGI(TAG, "Access Point IoT_net avviato!");

    // Avvia i task in parallelo
    xTaskCreate(coap_server_task, "coap_server_task", 8192, NULL, 5, NULL);
    xTaskCreate(lora_tx_task, "lora_tx_task", 8192, NULL, 5, NULL);
}