#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_adc/adc_oneshot.h"

#include "TDS.hpp"
#include "DS18B20.hpp"
#include "BH1750.hpp"
#include "aht20_bmp280.hpp"
#include "SoilMoistureSensor.hpp"

#include "config.hpp"

static const char *TAG = "APP_MAIN";

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
    if (err != ESP_OK) {
        return err;
    }

    return i2c_driver_install(I2C_MASTER_NUM, conf.mode, 0, 0, 0);
}

void setup() {
    ESP_LOGI(TAG, "Initializing ADC for TDS Sensor...");

    // Initialize the ADC Unit for TDS
    adc_oneshot_unit_handle_t adc_handle;
    adc_oneshot_unit_init_cfg_t init_config = {
        .unit_id = TDS_ADC_UNIT,
        .clk_src = ADC_RTC_CLK_SRC_DEFAULT,
        .ulp_mode = ADC_ULP_MODE_DISABLE,
    };
    ESP_ERROR_CHECK(adc_oneshot_new_unit(&init_config, &adc_handle));

    // Configure the ADC Channel
    adc_oneshot_chan_cfg_t config = {
        .atten = ADC_ATTEN_DB_12,       // 12dB attenuation allows reading voltages up to ~3.3V
        .bitwidth = ADC_BITWIDTH_DEFAULT,
    };
    ESP_ERROR_CHECK(adc_oneshot_config_channel(adc_handle, TDS_ADC_CHANNEL, &config));

    // Note: We are passing nullptr for the calibration handle here. 
    // For production, an adc_cali_handle_t should be generated and passed as the 3rd argument.
    tdsSensor = TDS(adc_handle, TDS_ADC_CHANNEL, nullptr);

    ESP_LOGI(TAG, "TDS Sensor Initialized.");

    // Initializing Soil Moisture Sensor
    ESP_LOGI(TAG, "Initializing Soil Moisture Sensor...");
    SoilMoistureSensor::Config soil_config = {
        .adc_unit = SOIL_MOISTURE_ADC_UNIT,
        .adc_channel = SOIL_MOISTURE_ADC_CHANNEL,
        .dry_value = SOIL_MOISTURE_DRY_VAL,
        .wet_value = SOIL_MOISTURE_WET_VAL
    };
    
    if (soilSensor.init(soil_config) != ESP_OK) {
        ESP_LOGE(TAG, "Failed to initialize Soil Moisture Sensor!");
    } else {
        ESP_LOGI(TAG, "Soil Moisture Sensor Initialized.");
    }

    // Initializing DS18B20 temperature sensor
    ESP_LOGI(TAG, "Initializing DS18B20 Temperature Sensor...");

    if (tempSensor.init() != ESP_OK) {
        ESP_LOGE(TAG, "Failed to initialize the DS18B20 pin. Halting task.");
        vTaskDelete(NULL);
    }

    tempSensor.setResolution(DS18B20::Resolution::RES_12_BIT);

    // Initializing I2C Master (Required for BH1750 and AHT20/BMP280)
    if (i2c_master_init() != ESP_OK) {
        ESP_LOGE(TAG, "Failed to initialize I2C master!");
        return;
    }

    // Initializing BH1750 light sensor
    ESP_LOGI(TAG, "Initializing BH1750 Light Sensor...");
    light_sensor.power_on();
    light_sensor.reset();

    // Initializing AHT20+BMP280
    ESP_LOGI(TAG, "Initializing AHT20 & BMP280 Sensors...");
    if (envSensor.init() != ESP_OK) {
        ESP_LOGW(TAG, "Failed to initialize AHT20/BMP280. Continuing anyway...");
    }

    ESP_LOGI(TAG, "Sensors initialized successfully. Starting continuous read loop.");
}

extern "C" void app_main(void)
{
    setup();

    while (true) {
        // Update system temperature
        float current_water_temp = tempSensor.readTemperatureC(); 
        tdsSensor.setTemperature(current_water_temp);

        // Read the ADC and calculate the TDS value
        esp_err_t err = tdsSensor.update();
        
        if (err == ESP_OK) {
            float tds_value = tdsSensor.getTdsValue();
            ESP_LOGI(TAG, "Water Temp: %.1f°C | TDS: %.0f ppm", current_water_temp, tds_value);
        } else {
            ESP_LOGE(TAG, "Failed to read from TDS sensor!");
        }

        // Read Soil Moisture
        float moisture_percentage = 0.0f;
        if (soilSensor.read_percentage(moisture_percentage) == ESP_OK) {
            ESP_LOGI(TAG, "Soil Moisture: %.1f%%", moisture_percentage);
        } else {
            ESP_LOGE(TAG, "Failed to read Soil Moisture sensor!");
        }

        // Read ambient light level
        light_sensor.setup_mode(mode);
        float lux = 0;

        if (light_sensor.read_lux(mode, lux) == ESP_OK) {
            ESP_LOGI(TAG, "Ambient Light: %.2f lx", lux);
        } else {
            ESP_LOGE(TAG, "Failed to read Light sensor!");
        }

        // AHT20+BMP280 read logic
        Aht20Bmp280::SensorData env_data;
        if (envSensor.read(env_data) == ESP_OK) {
            ESP_LOGI(TAG, "Air Temp: %.2f°C | Humid: %.2f%% | Press: %.2f hPa", 
                     env_data.aht_temperature, 
                     env_data.aht_humidity, 
                     env_data.bmp_pressure / 100.0f);
        } else {
            ESP_LOGE(TAG, "Failed to read AHT20/BMP280 sensor!");
        }

        printf("--------------------------------------------------\n"); // Added a divider for cleaner console output

        vTaskDelay(pdMS_TO_TICKS(LOOP_DELAY_MS));
    }
}