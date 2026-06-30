#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "DS18B20.hpp"

static const char *TAG = "DS18B20_MAIN";

#define DS18B20_DATA_PIN GPIO_NUM_4

extern "C" void app_main(void)
{
    ESP_LOGI(TAG, "Initializing DS18B20 Temperature Sensor...");

    DS18B20 tempSensor(DS18B20_DATA_PIN);

    if (tempSensor.init() != ESP_OK) {
        ESP_LOGE(TAG, "Failed to initialize the DS18B20 pin. Halting task.");
        vTaskDelete(NULL);
    }

    tempSensor.setResolution(DS18B20::Resolution::RES_12_BIT);

    ESP_LOGI(TAG, "Sensor initialized successfully. Starting continuous read loop.");

    while (1) {
        float temperatureC = tempSensor.readTemperatureC();

        if (temperatureC >= -55.0 && temperatureC <= 125.0) {
            ESP_LOGI(TAG, "Current Temperature: %.2f °C", temperatureC);
        } else {
            ESP_LOGW(TAG, "Invalid reading (%.2f °C). Check wiring and the 4.7kΩ pull-up resistor.", temperatureC);
        }

        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}