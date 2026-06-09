#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/i2c.h"
#include "bh1750.hpp"

// I2C Config
#define I2C_MASTER_NUM     I2C_NUM_0
#define I2C_MASTER_SDA_IO  21
#define I2C_MASTER_SCL_IO  22
#define I2C_MASTER_FREQ_HZ 400000

static esp_err_t i2c_master_init(void) {
    i2c_config_t conf = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = I2C_MASTER_SDA_IO,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_io_num = I2C_MASTER_SCL_IO,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = I2C_MASTER_FREQ_HZ,
    };

    esp_err_t err = i2c_param_config(I2C_MASTER_NUM, &conf);
    if (err != ESP_OK) {
        return err;
    }

    return i2c_driver_install(I2C_MASTER_NUM, conf.mode, 0, 0, 0);
}

void app_main(void) {
    if (i2c_master_init() != ESP_OK) {
        printf("Failed to initialize I2C master!\n");
        return;
    }
    
    bh1750_t light_sensor;
    bh1750_init(&light_sensor, I2C_MASTER_NUM, BH1750_I2C_ADDRESS_LO);

    bh1750_power_on(&light_sensor);
    bh1750_reset(&light_sensor);

    bh1750_mode_t mode = BH1750_MODE_ONE_TIME_H_RES;
    float lux = 0;

    while (1) {
        // Because the sensor is in ONE_TIME mode,it goes to sleep after measuring, so
        // we must re-issue the measurement command every time we want a new reading
        bh1750_setup_mode(&light_sensor, mode);

        if (bh1750_read_lux(&light_sensor, mode, &lux) == ESP_OK) {
            printf("Ambient Light: %.2f lx\n", lux);
        } else {
            printf("Failed to read sensor!\n");
        }

        vTaskDelay(pdMS_TO_TICKS(500));
    }
}