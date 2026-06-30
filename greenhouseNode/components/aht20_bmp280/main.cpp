#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/i2c.h"
#include "esp_log.h"

#include "aht20_bmp280.hpp"

#define I2C_MASTER_SCL_IO           22      
#define I2C_MASTER_SDA_IO           21      
#define I2C_MASTER_NUM              I2C_NUM_0 
#define I2C_MASTER_FREQ_HZ          400000  
#define I2C_MASTER_TX_BUF_DISABLE   0       
#define I2C_MASTER_RX_BUF_DISABLE   0       

static const char *TAG = "MAIN_APP";

static esp_err_t i2c_master_init(void) {
    i2c_config_t conf = {};
    conf.mode = I2C_MODE_MASTER;
    conf.sda_io_num = I2C_MASTER_SDA_IO;
    conf.scl_io_num = I2C_MASTER_SCL_IO;
    conf.sda_pullup_en = GPIO_PULLUP_ENABLE;
    conf.scl_pullup_en = GPIO_PULLUP_ENABLE;
    conf.master.clk_speed = I2C_MASTER_FREQ_HZ;

    i2c_param_config(I2C_MASTER_NUM, &conf);
    return i2c_driver_install(I2C_MASTER_NUM, conf.mode, I2C_MASTER_RX_BUF_DISABLE, I2C_MASTER_TX_BUF_DISABLE, 0);
}

extern "C" void app_main(void) {
    ESP_LOGI(TAG, "Initializing I2C...");
    ESP_ERROR_CHECK(i2c_master_init());

    ESP_LOGI(TAG, "Initializing Sensors...");
    
    Aht20Bmp280 sensor(I2C_MASTER_NUM, Aht20Bmp280::BMP280_I2C_ADDR_77);
    
    if (sensor.init() != ESP_OK) {
        ESP_LOGE(TAG, "Failed to initialize sensors. Check wiring and addresses.");
        return;
    }

    Aht20Bmp280::SensorData sensor_data;

    while (true) {
        if (sensor.read(sensor_data) == ESP_OK) {
            printf("\n--- Sensor Readings ---\n");
            printf("AHT20  Temp:  %.2f °C\n", sensor_data.aht_temperature);
            printf("AHT20  Humid: %.2f %%\n", sensor_data.aht_humidity);
            printf("BMP280 Temp:  %.2f °C\n", sensor_data.bmp_temperature);
            printf("BMP280 Press: %.2f hPa\n", sensor_data.bmp_pressure / 100.0f);
            printf("-----------------------\n");
        } else {
            ESP_LOGW(TAG, "Failed to read data from sensors");
        }

        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}