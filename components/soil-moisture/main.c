#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "soil_moisture.h"

static const char *TAG = "MAIN";

void app_main(void)
{
    soil_moisture_handle_t sensor_handle = NULL;

    // Configuration
    soil_moisture_config_t config = {
        .adc_unit = ADC_UNIT_1,
        .adc_channel = ADC_CHANNEL_4, 
        .dry_value = 3000,  // READ RAW VALUE IN DRY AIR AND PUT IT HERE
        .wet_value = 1200   // READ RAW VALUE IN A GLASS OF WATER AND PUT IT HERE
    };

    esp_err_t ret = soil_moisture_init(&config, &sensor_handle);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to initialize sensor");
        return;
    }

    while (1) {
        int raw_value = 0;
        float moisture_percent = 0.0f;

        soil_moisture_read_raw(sensor_handle, &raw_value);
        soil_moisture_read_percentage(sensor_handle, &moisture_percent);

        ESP_LOGI(TAG, "Raw ADC: %d | Moisture: %.1f%%", raw_value, moisture_percent);

        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}