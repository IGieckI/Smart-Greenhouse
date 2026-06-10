// #include <stdio.h>
// #include <string.h>
// #include "esp_wifi.h"
// #include "esp_event.h"
// #include "esp_mac.h"
// #include "esp_log.h"
// #include "nvs_flash.h"
// #include "esp_netif.h"
// #include "freertos/FreeRTOS.h"
// #include "freertos/task.h"
// #include "coap3/coap.h"

// #include "esp_random.h" // Aggiungi questo in cima per i numeri casuali


// #include <RadioLib.h>

// // --- DEFINIZIONE PIN RADIO HELTEC V3 ---
// #define RADIO_NSS    8
// #define RADIO_SCK    9
// #define RADIO_MOSI   10
// #define RADIO_MISO   11
// #define RADIO_RST    12
// #define RADIO_BUSY   13
// #define RADIO_DIO1   14


// #define AP_SSID       "IoT_net"
// #define AP_PASS       "1qaz2wsx"
// #define MAX_STA_CONN  4

// static const char *TAG = "HELTEC_GATEWAY";

// // --- CONFIGURAZIONE RADIOLIB ---
// // Adatta la classe in base al chip del tuo Heltec (probabilmente SX1262)
// SX1262 radio = new Module(RADIO_NSS, RADIO_DIO1, RADIO_RST, RADIO_BUSY);

// // Crea il nodo LoRaWAN usando la radio appena definita
// LoRaWANNode node(&radio, &ESP32_REGION_EU868); // Inserisci la tua region corretta (es. EU868)

// // Chiave di rete (spesso uguale a APPKEY in LoRaWAN 1.0.x, o separata in 1.1.x)
// static const uint8_t nwkKey[16] = { 0x3E, 0xE8, 0xD4, 0x07, 0x9C, 0x41, 0xA3, 0x93,
//                                     0x22, 0xAA, 0x5E, 0xF1, 0xE0, 0x34, 0x68, 0x33 };
// // ==============================================================================
// // --- CHIAVI LORAWAN (Copia qui i valori da The Things Network) ---
// // ATTENZIONE: Alcune librerie richiedono i byte in ordine inverso (LSB). 
// // Di default TTN li fornisce in MSB (Most Significant Byte).
// // ==============================================================================

// // Inserisci qui i tuoi 8 byte del DevEUI (es: {0x70, 0xB3, 0xD5, ...})
// static const uint8_t DEVEUI[8]  = { 0x70, 0xB3, 0xD5, 0x7E, 0xD0, 0x07, 0x7F, 0x3C };

// // Inserisci qui i tuoi 8 byte del JoinEUI (se hai messo zeri, lasciali così)
// static const uint8_t JOINEUI[8] = { 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00 };

// // Inserisci qui i tuoi 16 byte dell'AppKey (es: {0x1F, 0x2A, 0x3B, ...})
// static const uint8_t APPKEY[16] = { 0x3E, 0xE8, 0xD4, 0x07, 0x9C, 0x41, 0xA3, 0x93,
//                                     0x22, 0xAA, 0x5E, 0xF1, 0xE0, 0x34, 0x68, 0x33 };


// // --- PREDISPOSIZIONE LORAWAN (Goal 3 Completato) ---
// static void inoltra_su_lorawan(const uint8_t *dati, size_t lunghezza) {
//     ESP_LOGI(TAG, "--------------------------------------------------");
//     ESP_LOGI(TAG, "[LORA] Preparazione payload di %d byte per SX1262...", (int)lunghezza);
//     ESP_LOGI(TAG, "[LORA] Pacchetto da spedire: %.*s", (int)lunghezza, dati);
    
//     // RadioLib si aspetta un puntatore non-const per alcune sue funzioni interne,
//     // quindi facciamo un cast sicuro per passarlo alla funzione TX.
//     uint8_t* payload = const_cast<uint8_t*>(dati);

//     // Invio tramite lo stack LoRaWAN di RadioLib (sendReceive gestisce anche le finestre RX)
//     int16_t state = node.sendReceive(payload, lunghezza);

//     if (state == RADIOLIB_ERR_NONE) {
//         ESP_LOGI(TAG, "[LORA] >>> Inviato nell'etere via LoRaWAN! <<<");
//     } else if (state == RADIOLIB_LORAWAN_NONCE_OUT_OF_SYNC) {
//         ESP_LOGE(TAG, "[LORA] Errore: DevNonce fuori sincrono (richiede nuovo Join).");
//     } else {
//         ESP_LOGE(TAG, "[LORA] Errore di invio, codice: %d", state);
//     }
//     ESP_LOGI(TAG, "--------------------------------------------------");
// }

// // --- HANDLER CoAP (Ricezione Dati) ---
// static void coap_post_handler(coap_resource_t *resource,
//                               coap_session_t *session,
//                               const coap_pdu_t *request,
//                               const coap_string_t *query,
//                               coap_pdu_t *response) {
//     size_t size;
//     const uint8_t *data;
    
//     // 1. Estrae i dati dal pacchetto in entrata
//     if (coap_get_data(request, &size, &data)) {
//         // 2. Stampa a video il contenuto (Goal 1)
//         ESP_LOGI(TAG, ">>> Ricevuto pacchetto CoAP da dispositivo locale!");
        
//         // 3. Lo passa alla funzione pronta per la spedizione nel mondo (Goal 2)
//         inoltra_su_lorawan(data, size);
//     } else {
//         ESP_LOGW(TAG, "Ricevuto pacchetto CoAP vuoto o malformato");
//     }

//     // Risponde al dispositivo che il pacchetto è stato ricevuto (201 Created)
//     coap_pdu_set_code(response, COAP_RESPONSE_CODE_CREATED);
// }

// // --- TASK: SERVER CoAP ---
// static void coap_server_task(void *p) {
//     coap_context_t *ctx = NULL;
//     coap_address_t serv_addr;
//     coap_resource_t *resource = NULL;
//     coap_endpoint_t *ep = NULL;

//     coap_startup();
//     coap_address_init(&serv_addr);
//     serv_addr.addr.sin.sin_family = AF_INET;
//     serv_addr.addr.sin.sin_addr.s_addr = INADDR_ANY;
//     serv_addr.addr.sin.sin_port = htons(5683); 

//     ctx = coap_new_context(NULL);
//     ep = coap_new_endpoint(ctx, &serv_addr, COAP_PROTO_UDP);
    
//     resource = coap_resource_init(coap_make_str_const("dati"), 0);
//     coap_register_handler(resource, COAP_REQUEST_POST, coap_post_handler);
//     coap_add_resource(ctx, resource);

//     ESP_LOGI(TAG, "Gateway CoAP in ascolto...");

//     while (1) {
//         coap_io_process(ctx, 1000); 
//     }
// }

// // --- EVENT HANDLER WI-FI ---
// static void wifi_event_handler(void* arg, esp_event_base_t event_base, int32_t event_id, void* event_data) {
//     if (event_id == WIFI_EVENT_AP_STACONNECTED) {
//         wifi_event_ap_staconnected_t* event = (wifi_event_ap_staconnected_t*) event_data;
//         ESP_LOGI(TAG, "Dispositivo connesso! MAC: " MACSTR, MAC2STR(event->mac));
//     }
// }

// // --- INIZIALIZZAZIONE LORAWAN ---
// // Creiamo una funzione per tenere l'app_main pulito
// static void init_lorawan_stack() {
//     ESP_LOGI(TAG, "Inizializzazione modulo SX1262...");
//     int16_t state = radio.begin();
//     if (state != RADIOLIB_ERR_NONE) {
//         ESP_LOGE(TAG, "Inizializzazione radio fallita, codice: %d", state);
//         return;
//     }

//     ESP_LOGI(TAG, "Tentativo di Join OTAA alla rete LoRaWAN...");
//     // Passiamo le chiavi definite globalmente
//     state = node.beginOTAA(JOINEUI, DEVEUI,  nwkKey,  APPKEY);
    
//     if (state == RADIOLIB_LORAWAN_NEW_SESSION || state == RADIOLIB_ERR_NONE) {
//         ESP_LOGI(TAG, "Join OTAA effettuato con successo!");
//     } else {
//         ESP_LOGE(TAG, "Join OTAA fallito, codice: %d", state);
//     }
// }

// // // --- ENTRY POINT ---
// // void app_main(void) {
// //     esp_err_t ret = nvs_flash_init();
// //     if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
// //       ESP_ERROR_CHECK(nvs_flash_erase());
// //       nvs_flash_init();
// //     }

// //     ESP_ERROR_CHECK(esp_netif_init());
// //     ESP_ERROR_CHECK(esp_event_loop_create_default());
// //     esp_netif_create_default_wifi_ap();

// //     wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
// //     ESP_ERROR_CHECK(esp_wifi_init(&cfg));
// //     ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, NULL));

// //     wifi_config_t wifi_config = {
// //         .ap = {
// //             .ssid = AP_SSID,
// //             .ssid_len = strlen(AP_SSID),
// //             .channel = 1,
// //             .password = AP_PASS,
// //             .max_connection = MAX_STA_CONN,
// //             .authmode = WIFI_AUTH_WPA2_PSK
// //         },
// //     };

// //     ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
// //     ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &wifi_config));
// //     ESP_ERROR_CHECK(esp_wifi_start());

// //     ESP_LOGI(TAG, "Access Point IoT_net avviato!");

// //     // Inizializziamo il modulo LoRa ed eseguiamo la procedura di Join
// //     init_lorawan_stack();

// //     xTaskCreate(coap_server_task, "coap_server_task", 8192, NULL, 5, NULL);
// // }

// // --- ENTRY POINT ---
// // NOTA BENE: extern "C" è CRITICO per file .cpp in ESP-IDF
// extern "C" void app_main(void) {
//     esp_err_t ret = nvs_flash_init();
//     if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
//       ESP_ERROR_CHECK(nvs_flash_erase());
//       nvs_flash_init();
//     }

//     ESP_ERROR_CHECK(esp_netif_init());
//     ESP_ERROR_CHECK(esp_event_loop_create_default());
//     esp_netif_create_default_wifi_ap();

//     wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
//     ESP_ERROR_CHECK(esp_wifi_init(&cfg));
//     ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, NULL));

//     wifi_config_t wifi_config = {
//         .ap = {
//             .ssid = AP_SSID,
//             .ssid_len = strlen(AP_SSID),
//             .channel = 1,
//             .password = AP_PASS,
//             .max_connection = MAX_STA_CONN,
//             .authmode = WIFI_AUTH_WPA2_PSK
//         },
//     };

//     ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
//     ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &wifi_config));
//     ESP_ERROR_CHECK(esp_wifi_start());

//     ESP_LOGI(TAG, "Access Point IoT_net avviato!");

//     // Inizializziamo il modulo LoRa ed eseguiamo la procedura di Join
//     init_lorawan_stack();

//     xTaskCreate(coap_server_task, "coap_server_task", 8192, NULL, 5, NULL);
// }