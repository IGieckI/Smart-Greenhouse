#include <stdio.h>
#include <string.h>
#include <math.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_now.h"
#include "esp_netif.h"
#include "esp_mac.h"
#include "esp_sleep.h"
#include "nvs_flash.h"
#include "esp_adc/adc_oneshot.h"

#include "esp_timer.h"

#include "TDS.hpp"
#include "DS18B20.hpp"
#include "BH1750.hpp"
#include "aht20_bmp280.hpp"
#include "SoilMoistureSensor.hpp"
#include "config.hpp"
#include "TelemetryPacket.h"
#include "CommandPacket.h"

static const char *TAG = "GREENHOUSE_NODE";

// Network
telemetry_packet_t myData;
esp_now_peer_info_t peerInfo;

// Actuation command received from the Star
static command_packet_t received_command;
static SemaphoreHandle_t command_sem = NULL;

// Callback when a command is received
void OnCommandRecv(const esp_now_recv_info_t *info, const uint8_t *data, int len) {
    if (len == sizeof(command_packet_t)) {
        memcpy(&received_command, data, sizeof(command_packet_t));
        BaseType_t woken = pdFALSE;
        xSemaphoreGiveFromISR(command_sem, &woken);
        portYIELD_FROM_ISR(woken);
    }
}

// Callback when data is sent
void OnDataSent(const esp_now_send_info_t *info, esp_now_send_status_t status) {
    if(status != ESP_NOW_SEND_SUCCESS) {
        ESP_LOGE(TAG, "Last Packet Send Status: Delivery Fail");
    } else {
        ESP_LOGI(TAG, "Packet Delivered Successfully.");
    }
}

// Initialize Wi-Fi in Station Mode
static void wifi_init() {
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());
}

// Sensors stuff
TDS tdsSensor;
DS18B20 tempSensor(DS18B20_DATA_PIN);
BH1750 light_sensor(I2C_MASTER_NUM, BH1750::I2C_ADDRESS_LO);
BH1750::Mode mode = BH1750::Mode::ONE_TIME_H_RES;
Aht20Bmp280 envSensor(I2C_MASTER_NUM, Aht20Bmp280::BMP280_I2C_ADDR_77);
SoilMoistureSensor soilSensor; 

static esp_err_t i2c_master_init(void) {
    i2c_config_t conf = {};
    conf.mode = I2C_MODE_MASTER;
    conf.sda_io_num = I2C_MASTER_SDA_IO;
    conf.sda_pullup_en = GPIO_PULLUP_ENABLE;
    conf.scl_io_num = I2C_MASTER_SCL_IO;
    conf.scl_pullup_en = GPIO_PULLUP_ENABLE;
    conf.master.clk_speed = I2C_MASTER_FREQ_HZ;
    esp_err_t err = i2c_param_config(I2C_MASTER_NUM, &conf);
    if (err != ESP_OK) return err;
    return i2c_driver_install(I2C_MASTER_NUM, conf.mode, 0, 0, 0);
}

// Median Filter for sensor values
float get_median(float* data, int size) {
    float temp[size];
    memcpy(temp, data, size * sizeof(float));
    
    // Bubble Sort
    for (int i = 0; i < size - 1; i++) {
        for (int j = i + 1; j < size; j++) {
            if (temp[i] > temp[j]) {
                float t = temp[i];
                temp[i] = temp[j];
                temp[j] = t;
            }
        }
    }
    
    return temp[size / 2];
}

#ifdef ADS1115_ADDR
// ADC leaf temperature conversion reading
static int16_t read_ads1115(uint16_t config_flags) {

    uint8_t write_buf[3];
    write_buf[0] = ADS_POINTER_CONFIG;
    write_buf[1] = (config_flags >> 8) & 0xFF; 
    write_buf[2] = config_flags & 0xFF;        

    esp_err_t err;
    err = i2c_master_write_to_device(I2C_MASTER_NUM, ADS1115_ADDR, write_buf, 3, pdMS_TO_TICKS(1000));
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "I2C Write Error (Config): Check ADS1115 wiring.");
        return 0; 
    }

    vTaskDelay(pdMS_TO_TICKS(15));

    uint8_t reg = ADS_POINTER_CONVERSION;
    err = i2c_master_write_to_device(I2C_MASTER_NUM, ADS1115_ADDR, &reg, 1, pdMS_TO_TICKS(1000));
    if (err != ESP_OK) return 0;

    uint8_t read_buf[2] = {0, 0}; 
    err = i2c_master_read_from_device(I2C_MASTER_NUM, ADS1115_ADDR, read_buf, 2, pdMS_TO_TICKS(1000));
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "I2C Read Error (Data): ADS1115 did not respond.");
        return 0; 
    }

    return (int16_t)((read_buf[0] << 8) | read_buf[1]);
}
#endif

float get_leaf_temp(float ambient_temp) {
    uint16_t config_tc = 0x8F83; 
    #ifdef ADS1115_ADDR
    int16_t raw_tc = read_ads1115(config_tc);
    #else
    int16_t raw_tc = 0;
    #endif

    float tc_voltage_mv = raw_tc * 0.0078125;
    float delta_temp = tc_voltage_mv / 0.041276;

    return ambient_temp + delta_temp;
}

void setup() {
    // Initialize NVS (required for Wi-Fi)
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    // Initialize Wi-Fi and ESP-NOW
    wifi_init();
    if (esp_now_init() != ESP_OK) {
        ESP_LOGE(TAG, "Error initializing ESP-NOW");
        return;
    }
   
    esp_now_register_send_cb(OnDataSent);
    esp_now_register_recv_cb(OnCommandRecv);
    memset(&peerInfo, 0, sizeof(peerInfo));
    memcpy(peerInfo.peer_addr, central_mac, 6);
    peerInfo.channel = 1;
    peerInfo.encrypt = false;

    if (esp_now_add_peer(&peerInfo) != ESP_OK){
        ESP_LOGE(TAG, "Failed to add peer");
        return;
    }

    // Set Node ID using MAC Address (last 4 bytes of the MAC address are used)
    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    uint32_t mac_id = ((uint32_t)mac[2] << 24) | ((uint32_t)mac[3] << 16) | ((uint32_t)mac[4] << 8) | (uint32_t)mac[5];
    myData.node_id = mac_id;
    ESP_LOGI(TAG, "Device Node ID set to: %u", mac_id);

    // Initialize Sensors
    adc_oneshot_unit_handle_t shared_adc_handle;
    adc_oneshot_unit_init_cfg_t init_config = {
        .unit_id = TDS_ADC_UNIT,
        .clk_src = ADC_RTC_CLK_SRC_DEFAULT,
        .ulp_mode = ADC_ULP_MODE_DISABLE,
    };
    ESP_ERROR_CHECK(adc_oneshot_new_unit(&init_config, &shared_adc_handle));

    adc_oneshot_chan_cfg_t tds_config = {
        .atten = ADC_ATTEN_DB_12, 
        .bitwidth = ADC_BITWIDTH_DEFAULT,
    };
    ESP_ERROR_CHECK(adc_oneshot_config_channel(shared_adc_handle, TDS_ADC_CHANNEL, &tds_config));
    tdsSensor = TDS(shared_adc_handle, TDS_ADC_CHANNEL, nullptr);

    SoilMoistureSensor::Config soil_config = {
        .adc_handle = shared_adc_handle,
        .adc_channel = SOIL_MOISTURE_ADC_CHANNEL,
        .dry_value = SOIL_MOISTURE_DRY_VAL,
        .wet_value = SOIL_MOISTURE_WET_VAL
    };
    soilSensor.init(soil_config);

    tempSensor.init();
    tempSensor.setResolution(DS18B20::Resolution::RES_12_BIT);

    i2c_master_init();

    light_sensor.power_on();
    light_sensor.reset();

    envSensor.init();

    ledc_timer_config_t ledc_timer = {
        .speed_mode      = LEDC_LOW_SPEED_MODE,
        .duty_resolution = LEDC_TIMER_10_BIT,
        .timer_num       = LEDC_TIMER_0,
        .freq_hz         = 1000,
        .clk_cfg         = LEDC_AUTO_CLK,
    };
    ledc_timer_config(&ledc_timer);

    for (size_t i = 0; i < ACTUATOR_COUNT; i++) {
        gpio_config_t io = {};
        io.intr_type    = GPIO_INTR_DISABLE;
        io.mode         = GPIO_MODE_OUTPUT;
        io.pin_bit_mask = (1ULL << ACTUATOR_TABLE[i].pin);
        io.pull_down_en = GPIO_PULLDOWN_DISABLE;
        io.pull_up_en   = GPIO_PULLUP_DISABLE;
        gpio_config(&io);

        if (ACTUATOR_TABLE[i].type == ACT_PWM) {
            ledc_channel_config_t ch = {
                .gpio_num   = ACTUATOR_TABLE[i].pin,
                .speed_mode = LEDC_LOW_SPEED_MODE,
                .channel    = ACTUATOR_TABLE[i].ledc_ch,
                .intr_type  = LEDC_INTR_DISABLE,
                .timer_sel  = LEDC_TIMER_0,
                .duty       = 0,
                .hpoint     = 0,
            };
            ledc_channel_config(&ch);
        } else {
            gpio_set_level(ACTUATOR_TABLE[i].pin, 0);
        }
    }

    ESP_LOGI(TAG, "Sensors initialized. Preparing to sample...");
}

extern "C" void app_main(void)
{
    setup();

    // Arrays to hold samples for median calculation
    float water_temps[NUM_SAMPLES];
    float tds_values[NUM_SAMPLES];
    float moistures[NUM_SAMPLES];
    float luxes[NUM_SAMPLES];
    float air_temps[NUM_SAMPLES];
    float humidities[NUM_SAMPLES];
    float pressures[NUM_SAMPLES];
    float leaf_temps[NUM_SAMPLES];

    // Take multiple readings to populate the arrays
    for(int i = 0; i < NUM_SAMPLES; i++) {
        water_temps[i] = tempSensor.readTemperatureC(); 
        tdsSensor.setTemperature(water_temps[i]);
        tdsSensor.update();
        tds_values[i] = tdsSensor.getTdsValue();
       
        soilSensor.read_percentage(moistures[i]);
        
        light_sensor.setup_mode(mode);
        light_sensor.read_lux(mode, luxes[i]);
       
        Aht20Bmp280::SensorData env_data;
        envSensor.read(env_data);
        air_temps[i] = env_data.aht_temperature;
        humidities[i] = env_data.aht_humidity;
        pressures[i] = env_data.bmp_pressure;

#ifdef ADS1115_ADDR
        leaf_temps[i] = get_leaf_temp(env_data.aht_temperature);
#else
        leaf_temps[i] = 0;
#endif
        vTaskDelay(pdMS_TO_TICKS(SAMPLE_DELAY_MS));
    }

    myData.water_temp = get_median(water_temps, NUM_SAMPLES);
    myData.tds_value = get_median(tds_values, NUM_SAMPLES);
    myData.soil_moisture = get_median(moistures, NUM_SAMPLES);
    myData.light_lux = get_median(luxes, NUM_SAMPLES);
    myData.air_temp = get_median(air_temps, NUM_SAMPLES);
    myData.humidity = get_median(humidities, NUM_SAMPLES);
    myData.pressure = get_median(pressures, NUM_SAMPLES);
    myData.leaf_temp = get_median(leaf_temps, NUM_SAMPLES);

    printf("{\"node_id\":%lu, \"water_temp\":%.2f, \"tds\":%.0f, \"soil_moisture\":%.1f, \"light_lux\":%.2f, \"air_temp\":%.2f, \"humidity\":%.2f, \"pressure\":%.2f, \"leaf_temp\":%.2f}\n",
        myData.node_id,
        myData.water_temp,
        myData.tds_value,
        myData.soil_moisture,
        myData.light_lux,
        myData.air_temp,
        myData.humidity,
        myData.pressure,
        myData.leaf_temp
    );

    // Send over ESP-NOW
    command_sem = xSemaphoreCreateBinary();
    esp_now_send(central_mac, (uint8_t *)&myData, sizeof(myData));

    // Wait up to 1.5s for an actuation command from the Star.
    if (xSemaphoreTake(command_sem, pdMS_TO_TICKS(1500)) == pdTRUE) {
        ESP_LOGI(TAG, "Command: %.*s val=%d dur=%ds",
                 CMD_ACTUATOR_LEN, received_command.actuator,
                 received_command.value, received_command.duration_s);

        const actuator_cfg_t *act = NULL;
        for (size_t i = 0; i < ACTUATOR_COUNT; i++) {
            if (strncmp(received_command.actuator, ACTUATOR_TABLE[i].name, CMD_ACTUATOR_LEN) == 0) {
                act = &ACTUATOR_TABLE[i];
                break;
            }
        }

        if (act == NULL) {
            ESP_LOGW(TAG, "Unknown actuator: %.*s", CMD_ACTUATOR_LEN, received_command.actuator);
        } else if (act->type == ACT_BINARY) {
            gpio_set_level(act->pin, received_command.value > 0 ? 1 : 0);
            if (received_command.value > 0 && received_command.duration_s > 0) {
                vTaskDelay(pdMS_TO_TICKS((uint32_t)received_command.duration_s * 1000));
                gpio_set_level(act->pin, 0);
            }
        } else {
            uint32_t duty = ((uint32_t)received_command.value * 1023) / 255;
            ledc_set_duty(LEDC_LOW_SPEED_MODE, act->ledc_ch, duty);
            ledc_update_duty(LEDC_LOW_SPEED_MODE, act->ledc_ch);
            if (received_command.duration_s > 0) {
                vTaskDelay(pdMS_TO_TICKS((uint32_t)received_command.duration_s * 1000));
                ledc_set_duty(LEDC_LOW_SPEED_MODE, act->ledc_ch, 0);
                ledc_update_duty(LEDC_LOW_SPEED_MODE, act->ledc_ch);
            }
        }
    }

    // Get milliseconds since boot to calculate how long the node has been awake
    int64_t awake_time_ms = esp_timer_get_time() / 1000; 
    int64_t sleep_time_ms = DEEP_SLEEP_MS - awake_time_ms;

    // Check if the execution time exceeded DEEP_SLEEP_MS to avoid negative sleep time
    if (sleep_time_ms < 0) {
        sleep_time_ms = 0; 
    }

    ESP_LOGI(TAG, "Awake time: %lld ms, entering deep sleep mode for %lld ms...", awake_time_ms, sleep_time_ms);
    
    esp_sleep_enable_timer_wakeup((uint64_t)sleep_time_ms * 1000ULL);
    esp_deep_sleep_start();
}