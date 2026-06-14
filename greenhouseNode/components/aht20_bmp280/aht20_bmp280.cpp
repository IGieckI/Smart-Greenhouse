#include "aht20_bmp280.hpp"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"

static const char *TAG = "AHT20_BMP280_CLASS";

Aht20Bmp280::Aht20Bmp280(i2c_port_t port, uint8_t bmp280_addr) 
    : _port(port), _bmp_addr(bmp280_addr), _calib({}), _t_fine(0) {}

esp_err_t Aht20Bmp280::write_reg(uint8_t addr, uint8_t reg, uint8_t data) {
    uint8_t write_buf[2] = {reg, data};
    return i2c_master_write_to_device(_port, addr, write_buf, sizeof(write_buf), pdMS_TO_TICKS(100));
}

esp_err_t Aht20Bmp280::bmp280_read_calib() {
    uint8_t buf[24];
    uint8_t reg = 0x88;
    esp_err_t err = i2c_master_write_read_device(_port, _bmp_addr, &reg, 1, buf, 24, pdMS_TO_TICKS(100));
    if (err != ESP_OK) return err;

    _calib.dig_T1 = (buf[1] << 8) | buf[0];
    _calib.dig_T2 = (buf[3] << 8) | buf[2];
    _calib.dig_T3 = (buf[5] << 8) | buf[4];
    _calib.dig_P1 = (buf[7] << 8) | buf[6];
    _calib.dig_P2 = (buf[9] << 8) | buf[8];
    _calib.dig_P3 = (buf[11] << 8) | buf[10];
    _calib.dig_P4 = (buf[13] << 8) | buf[12];
    _calib.dig_P5 = (buf[15] << 8) | buf[14];
    _calib.dig_P6 = (buf[17] << 8) | buf[16];
    _calib.dig_P7 = (buf[19] << 8) | buf[18];
    _calib.dig_P8 = (buf[21] << 8) | buf[20];
    _calib.dig_P9 = (buf[23] << 8) | buf[22];
    return ESP_OK;
}

float Aht20Bmp280::bmp280_compensate_T(int32_t adc_T) {
    int32_t var1, var2, T;
    var1 = ((((adc_T >> 3) - ((int32_t)_calib.dig_T1 << 1))) * ((int32_t)_calib.dig_T2)) >> 11;
    var2 = (((((adc_T >> 4) - ((int32_t)_calib.dig_T1)) * ((adc_T >> 4) - ((int32_t)_calib.dig_T1))) >> 12) * ((int32_t)_calib.dig_T3)) >> 14;
    _t_fine = var1 + var2;
    T = (_t_fine * 5 + 128) >> 8;
    return (float)T / 100.0f;
}

float Aht20Bmp280::bmp280_compensate_P(int32_t adc_P) {
    int64_t var1, var2, p;
    var1 = ((int64_t)_t_fine) - 128000;
    var2 = var1 * var1 * (int64_t)_calib.dig_P6;
    var2 = var2 + ((var1 * (int64_t)_calib.dig_P5) << 17);
    var2 = var2 + (((int64_t)_calib.dig_P4) << 35);
    var1 = ((var1 * var1 * (int64_t)_calib.dig_P3) >> 8) + ((var1 * (int64_t)_calib.dig_P2) << 12);
    var1 = (((((int64_t)1) << 47) + var1)) * ((int64_t)_calib.dig_P1) >> 33;
    if (var1 == 0) return 0; // Avoid division by zero
    p = 1048576 - adc_P;
    p = (((p << 31) - var2) * 3125) / var1;
    var1 = (((int64_t)_calib.dig_P9) * (p >> 13) * (p >> 13)) >> 25;
    var2 = (((int64_t)_calib.dig_P8) * p) >> 19;
    p = ((p + var1 + var2) >> 8) + (((int64_t)_calib.dig_P7) << 4);
    return (float)p / 256.0f;
}

esp_err_t Aht20Bmp280::init() {
    // 1. Initialize AHT20
    vTaskDelay(pdMS_TO_TICKS(40)); // Wait for AHT20 power on
    uint8_t aht_init_cmd[] = {0xBE, 0x08, 0x00};
    esp_err_t err = i2c_master_write_to_device(_port, AHT20_I2C_ADDRESS, aht_init_cmd, sizeof(aht_init_cmd), pdMS_TO_TICKS(100));
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "AHT20 init failed");
        return err;
    }

    // 2. Initialize BMP280
    uint8_t bmp_id = 0;
    uint8_t id_reg = 0xD0;
    err = i2c_master_write_read_device(_port, _bmp_addr, &id_reg, 1, &bmp_id, 1, pdMS_TO_TICKS(100));
    if (err != ESP_OK || bmp_id != 0x58) {
        ESP_LOGE(TAG, "BMP280 not found or wrong ID: 0x%02X", bmp_id);
        return ESP_FAIL;
    }

    err = bmp280_read_calib();
    if (err != ESP_OK) return err;

    // Set BMP280 config: Normal mode, Temp OSx1, Press OSx1 (0x27)
    err = write_reg(_bmp_addr, 0xF4, 0x27);
    if (err != ESP_OK) return err;
    
    // Set Standby 1000ms, Filter x16 (0xA0)
    err = write_reg(_bmp_addr, 0xF5, 0xA0);
    
    ESP_LOGI(TAG, "AHT20 & BMP280 Initialized successfully");
    return err;
}

esp_err_t Aht20Bmp280::read(SensorData& out_data) {
    esp_err_t err;

    // --- Read AHT20 ---
    uint8_t aht_trigger[] = {0xAC, 0x33, 0x00};
    err = i2c_master_write_to_device(_port, AHT20_I2C_ADDRESS, aht_trigger, sizeof(aht_trigger), pdMS_TO_TICKS(100));
    if (err == ESP_OK) {
        vTaskDelay(pdMS_TO_TICKS(80)); // Wait for measurement
        uint8_t aht_data[6];
        err = i2c_master_read_from_device(_port, AHT20_I2C_ADDRESS, aht_data, 6, pdMS_TO_TICKS(100));
        
        if (err == ESP_OK && (aht_data[0] & 0x80) == 0) { 
            uint32_t h = ((uint32_t)aht_data[1] << 12) | ((uint32_t)aht_data[2] << 4) | (aht_data[3] >> 4);
            out_data.aht_humidity = ((float)h / 1048576.0f) * 100.0f;

            uint32_t t = (((uint32_t)aht_data[3] & 0x0F) << 16) | ((uint32_t)aht_data[4] << 8) | aht_data[5];
            out_data.aht_temperature = ((float)t / 1048576.0f) * 200.0f - 50.0f;
        }
    }

    // --- Read BMP280 ---
    uint8_t bmp_data[6];
    uint8_t reg = 0xF7;
    err = i2c_master_write_read_device(_port, _bmp_addr, &reg, 1, bmp_data, 6, pdMS_TO_TICKS(100));
    if (err == ESP_OK) {
        int32_t adc_P = (bmp_data[0] << 12) | (bmp_data[1] << 4) | (bmp_data[2] >> 4);
        int32_t adc_T = (bmp_data[3] << 12) | (bmp_data[4] << 4) | (bmp_data[5] >> 4);

        out_data.bmp_temperature = bmp280_compensate_T(adc_T);
        out_data.bmp_pressure = bmp280_compensate_P(adc_P);
    }

    return err;
}