#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_adc/adc_oneshot.h"

#include "TDS.hpp"
#include "DS18B20.hpp"

static const char *TAG = "APP_MAIN";

// TDS ADC unit and channel the sensor is connected to.
#define TDS_ADC_UNIT    ADC_UNIT_1
#define TDS_ADC_CHANNEL ADC_CHANNEL_0

// DS18B20 GPIO pin
#define DS18B20_DATA_PIN GPIO_NUM_32

extern "C" void app_main(void)
{
    ESP_LOGI(TAG, "Initializing ADC for TDS Sensor...");

    // Initialize the ADC Unit
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
    TDS tdsSensor(adc_handle, TDS_ADC_CHANNEL, nullptr);

    ESP_LOGI(TAG, "TDS Sensor Initialized. Starting read loop...");

    // Initializing DS18B20 temperature sensor
    ESP_LOGI(TAG, "Initializing DS18B20 Temperature Sensor...");

    DS18B20 tempSensor(DS18B20_DATA_PIN);

    // 2. Initialize the hardware pin configuration
    if (tempSensor.init() != ESP_OK) {
        ESP_LOGE(TAG, "Failed to initialize the DS18B20 pin. Halting task.");
        vTaskDelete(NULL);
    }

    // 3. Set the sensor resolution
    // 12-bit is the maximum resolution and takes up to 750ms to convert[cite: 7].
    tempSensor.setResolution(DS18B20::Resolution::RES_12_BIT);

    ESP_LOGI(TAG, "Sensor initialized successfully. Starting continuous read loop.");


    while (true) {
        // Optional: update the temperature through temperature sensor
        float current_water_temp = tempSensor.readTemperatureC(); 
        tdsSensor.setTemperature(current_water_temp);

        // Read the ADC and calculate the TDS value
        esp_err_t err = tdsSensor.update();
        if (err == ESP_OK) {
            float tds_value = tdsSensor.getTdsValue();
            ESP_LOGI(TAG, "Water Temperature: %.1f°C | TDS Value: %.0f ppm", current_water_temp, tds_value);
        } else {
            ESP_LOGE(TAG, "Failed to read from TDS sensor!");
        }

        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}