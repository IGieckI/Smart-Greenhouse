#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "DS18B20.hpp"

static const char *TAG = "DS18B20_MAIN";

// Define the GPIO pin where the Yellow DATA wire is connected.
// Note: Standard 1-Wire protocol requires a 4.7kΩ pull-up resistor 
// between the DATA pin and VCC (3.3V or 5V).
#define DS18B20_DATA_PIN GPIO_NUM_4

extern "C" void app_main(void)
{
    ESP_LOGI(TAG, "Initializing DS18B20 Temperature Sensor...");

    // 1. Instantiate the sensor object
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

    // 4. Main application loop
    while (1) {
        // Request the temperature reading
        float temperatureC = tempSensor.readTemperatureC();

        // Validate the reading against the hardware's capabilities
        // The datasheet specifies an operating range of -55°C to +125°C[cite: 8].
        if (temperatureC >= -55.0 && temperatureC <= 125.0) {
            ESP_LOGI(TAG, "Current Temperature: %.2f °C", temperatureC);
        } else {
            ESP_LOGW(TAG, "Invalid reading (%.2f °C). Check wiring and the 4.7kΩ pull-up resistor.", temperatureC);
        }

        // Wait 2 seconds before requesting the next reading
        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}