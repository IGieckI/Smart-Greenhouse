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

// #define AP_SSID       "IoT_net"
// #define AP_PASS       "1qaz2wsx"
// #define MAX_STA_CONN  4

// static const char *TAG = "HELTEC_GATEWAY";

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
//         ESP_LOGI(TAG, "==================================================");
//         ESP_LOGI(TAG, ">>> Ricevuto pacchetto CoAP da dispositivo locale!");
//         ESP_LOGI(TAG, "[DATI RICEVUTI]: %.*s", (int)size, data);
//         ESP_LOGI(TAG, "==================================================");
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

// // --- ENTRY POINT ---
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

//     // wifi_config_t wifi_config = {
//     //     .ap = {
//     //         .ssid = (uint8_t*)AP_SSID,
//     //         .password = (uint8_t*)AP_PASS,
//     //         .ssid_len = strlen(AP_SSID),
//     //         .channel = 1,
//     //         .max_connection = MAX_STA_CONN,
//     //         .authmode = WIFI_AUTH_WPA2_PSK
//     //     },
//     // };

//     // 1. Inizializza la struttura a zero
//     wifi_config_t wifi_config = {}; 

//     // 2. Copia in modo sicuro le stringhe negli array di tipo uint8_t
//     memcpy(wifi_config.ap.ssid, AP_SSID, strlen(AP_SSID));
//     memcpy(wifi_config.ap.password, AP_PASS, strlen(AP_PASS));
    
//     // 3. Assegna i valori rimanenti
//     wifi_config.ap.ssid_len = strlen(AP_SSID);
//     wifi_config.ap.channel = 1;
//     wifi_config.ap.authmode = WIFI_AUTH_WPA2_PSK;
//     wifi_config.ap.max_connection = MAX_STA_CONN;

//     //  wifi_config_t wifi_config = {
//     //     .ap = {
//     //         .ssid = AP_SSID,
//     //         .ssid_len = strlen(AP_SSID),
//     //         .channel = 1,
//     //         .password = AP_PASS,
//     //         .max_connection = MAX_STA_CONN,
//     //         .authmode = WIFI_AUTH_WPA2_PSK
//     //     },
//     // };

//     ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
//     ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &wifi_config));
//     ESP_ERROR_CHECK(esp_wifi_start());

//     ESP_LOGI(TAG, "Access Point IoT_net avviato!");

//     // Avvia il server CoAP su un thread separato
//     xTaskCreate(coap_server_task, "coap_server_task", 8192, NULL, 5, NULL);
// }