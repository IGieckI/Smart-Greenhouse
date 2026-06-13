// #include <stdio.h>
// #include <string.h>
// #include "freertos/FreeRTOS.h"
// #include "freertos/task.h"
// #include "esp_log.h"
// #include "esp_wifi.h"
// #include "esp_now.h"
// #include "esp_netif.h"
// #include "nvs_flash.h"
// #include "esp_adc/adc_oneshot.h"

// // Your existing sensor includes
// #include "TDS.hpp"
// #include "DS18B20.hpp"
// #include "BH1750.hpp"
// #include "aht20_bmp280.hpp"
// #include "SoilMoistureSensor.hpp"
// #include "config.hpp"
// #include "TelemetryPacket.h"

// static const char *TAG = "PERIPHERAL_NODE_" XSTR(NODE_ID);

// // Network stuff
// telemetry_packet_t myData;
// esp_now_peer_info_t peerInfo;

// // Callback when data is sent
// void OnDataSent(const esp_now_send_info_t *info, esp_now_send_status_t status) {
//     ESP_LOGI(TAG, "Last Packet Send Status: %s", status == ESP_NOW_SEND_SUCCESS ? "Delivery Success" : "Delivery Fail");
// }

// // Initialize Wi-Fi in Station Mode
// static void wifi_init() {
//     ESP_ERROR_CHECK(esp_netif_init());
//     ESP_ERROR_CHECK(esp_event_loop_create_default());
//     wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
//     ESP_ERROR_CHECK(esp_wifi_init(&cfg));
//     ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
//     ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
//     ESP_ERROR_CHECK(esp_wifi_start());
// }

// // Sensors stuff
// TDS tdsSensor;
// DS18B20 tempSensor(DS18B20_DATA_PIN);
// BH1750 light_sensor(I2C_MASTER_NUM, BH1750::I2C_ADDRESS_LO);
// BH1750::Mode mode = BH1750::Mode::ONE_TIME_H_RES;
// Aht20Bmp280 envSensor(I2C_MASTER_NUM, Aht20Bmp280::BMP280_I2C_ADDR_77);
// SoilMoistureSensor soilSensor; 

// static esp_err_t i2c_master_init(void) {
//     i2c_config_t conf = {};
//     conf.mode = I2C_MODE_MASTER;
//     conf.sda_io_num = I2C_MASTER_SDA_IO;
//     conf.sda_pullup_en = GPIO_PULLUP_ENABLE;
//     conf.scl_io_num = I2C_MASTER_SCL_IO;
//     conf.scl_pullup_en = GPIO_PULLUP_ENABLE;
//     conf.master.clk_speed = I2C_MASTER_FREQ_HZ;
//     esp_err_t err = i2c_param_config(I2C_MASTER_NUM, &conf);
//     if (err != ESP_OK) return err;
//     return i2c_driver_install(I2C_MASTER_NUM, conf.mode, 0, 0, 0);
// }


// void setup() {
//     // Initialize NVS (required for Wi-Fi)
//     esp_err_t ret = nvs_flash_init();
//     if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
//         ESP_ERROR_CHECK(nvs_flash_erase());
//         ret = nvs_flash_init();
//     }
//     ESP_ERROR_CHECK(ret);

//     // Initialize Wi-Fi and ESP-NOW
//     wifi_init();
//     if (esp_now_init() != ESP_OK) {
//         ESP_LOGE(TAG, "Error initializing ESP-NOW");
//         return;
//     }
    
//     // Register callback and peer
//     esp_now_register_send_cb(OnDataSent);
//     memset(&peerInfo, 0, sizeof(peerInfo));
//     memcpy(peerInfo.peer_addr, central_mac, 6);
//     peerInfo.channel = 0;  
//     peerInfo.encrypt = false;

//     if (esp_now_add_peer(&peerInfo) != ESP_OK){
//         ESP_LOGE(TAG, "Failed to add peer");
//         return;
//     }

//     ESP_LOGI(TAG, "Initializing Shared ADC Unit...");

//     // Initialize the ADC Unit
//     adc_oneshot_unit_handle_t shared_adc_handle;
//     adc_oneshot_unit_init_cfg_t init_config = {
//         .unit_id = TDS_ADC_UNIT,
//         .clk_src = ADC_RTC_CLK_SRC_DEFAULT,
//         .ulp_mode = ADC_ULP_MODE_DISABLE,
//     };
//     ESP_ERROR_CHECK(adc_oneshot_new_unit(&init_config, &shared_adc_handle));

//     // Configure the ADC Channel for TDS
//     adc_oneshot_chan_cfg_t tds_config = {
//         .atten = ADC_ATTEN_DB_12,       // 12dB attenuation allows reading voltages up to ~3.3V
//         .bitwidth = ADC_BITWIDTH_DEFAULT,
//     };
//     ESP_ERROR_CHECK(adc_oneshot_config_channel(shared_adc_handle, TDS_ADC_CHANNEL, &tds_config));

//     tdsSensor = TDS(shared_adc_handle, TDS_ADC_CHANNEL, nullptr);
//     ESP_LOGI(TAG, "TDS Sensor Initialized.");

//     // Configure Soil Moisture using the same shared handle
//     ESP_LOGI(TAG, "Initializing Soil Moisture Sensor...");
//     SoilMoistureSensor::Config soil_config = {
//         .adc_handle = shared_adc_handle,
//         .adc_channel = SOIL_MOISTURE_ADC_CHANNEL,
//         .dry_value = SOIL_MOISTURE_DRY_VAL,
//         .wet_value = SOIL_MOISTURE_WET_VAL
//     };
    
//     if (soilSensor.init(soil_config) != ESP_OK) {
//         ESP_LOGE(TAG, "Failed to initialize Soil Moisture Sensor!");
//     } else {
//         ESP_LOGI(TAG, "Soil Moisture Sensor Initialized.");
//     }

//     // Initializing DS18B20 temperature sensor
//     ESP_LOGI(TAG, "Initializing DS18B20 Temperature Sensor...");

//     if (tempSensor.init() != ESP_OK) {
//         ESP_LOGE(TAG, "Failed to initialize the DS18B20 pin. Halting task.");
//         vTaskDelete(NULL);
//     }

//     tempSensor.setResolution(DS18B20::Resolution::RES_12_BIT);

//     // Initializing I2C Master (Required for BH1750 and AHT20/BMP280)
//     if (i2c_master_init() != ESP_OK) {
//         ESP_LOGE(TAG, "Failed to initialize I2C master!");
//         return;
//     }

//     // Initializing BH1750 light sensor
//     ESP_LOGI(TAG, "Initializing BH1750 Light Sensor...");
//     light_sensor.power_on();
//     light_sensor.reset();

//     // Initializing AHT20+BMP280
//     ESP_LOGI(TAG, "Initializing AHT20 & BMP280 Sensors...");
//     if (envSensor.init() != ESP_OK) {
//         ESP_LOGW(TAG, "Failed to initialize AHT20/BMP280. Continuing anyway...");
//     }

//     ESP_LOGI(TAG, "Sensors initialized successfully. Starting continuous read loop.");
// }

// extern "C" void app_main(void)
// {
//     setup();

//     myData.node_id = NODE_ID;

//     while (true) {
//         // Read Sensors
//         float current_water_temp = tempSensor.readTemperatureC(); 
//         tdsSensor.setTemperature(current_water_temp);
//         tdsSensor.update();
        
//         float moisture_percentage = 0.0f;
//         soilSensor.read_percentage(moisture_percentage);
        
//         float lux = 0;
//         light_sensor.setup_mode(mode);
//         light_sensor.read_lux(mode, lux);
        
//         Aht20Bmp280::SensorData env_data;
//         envSensor.read(env_data);

//         // Populate Payload
//         myData.water_temp = current_water_temp;
//         myData.tds_value = tdsSensor.getTdsValue();
//         myData.soil_moisture = moisture_percentage;
//         myData.light_lux = lux;
//         myData.air_temp = env_data.aht_temperature;
//         myData.humidity = env_data.aht_humidity;
//         myData.pressure = env_data.bmp_pressure;

//         printf("{\"node_id\":%d, \"water_temp\":%.2f, \"tds\":%.0f, \"soil_moisture\":%.1f, \"light_lux\":%.2f, \"air_temp\":%.2f, \"humidity\":%.2f, \"pressure\":%.2f}\n",
//             myData.node_id,
//             myData.water_temp,
//             myData.tds_value,
//             myData.soil_moisture,
//             myData.light_lux,
//             myData.air_temp,
//             myData.humidity,
//             myData.pressure
//         );

//         // Send over ESP-NOW
//         esp_err_t result = esp_now_send(central_mac, (uint8_t *) &myData, sizeof(myData));
        
//         // Print the data
//         if (result == ESP_OK) {
//             ESP_LOGI(TAG, "Sent data successfully");
//         } else {
//             ESP_LOGE(TAG, "Error sending the data");
//         }

//         vTaskDelay(pdMS_TO_TICKS(LOOP_DELAY_MS));
//     }
// }