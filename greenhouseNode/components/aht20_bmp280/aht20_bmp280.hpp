#pragma once

#include "driver/i2c.h"
#include "esp_err.h"

/**
 * @ref Based on Seeed-Studio/Seeed_Arduino_AHT20 and adafruit/adafruit_bmp280_library implementation
 */
class Aht20Bmp280 {
public:
    static constexpr uint8_t AHT20_I2C_ADDRESS = 0x38;
    static constexpr uint8_t BMP280_I2C_ADDR_77 = 0x77;

    struct SensorData {
        float aht_temperature;  // Celsius
        float aht_humidity;     // Percentage
        float bmp_temperature;  // Celsius
        float bmp_pressure;     // Pascals
    };

    /**
     * @brief Constructor
     * @param port I2C port number
     * @param bmp280_addr I2C address of the BMP280
     */
    Aht20Bmp280(i2c_port_t port = I2C_NUM_0, uint8_t bmp280_addr = BMP280_I2C_ADDR_77);

    /**
     * @brief Initialize both AHT20 and BMP280 sensors
     * @return ESP_OK on success
     */
    esp_err_t init();

    /**
     * @brief Read temperature, humidity, and pressure from the sensors
     * @param out_data Reference to struct to hold sensor data
     * @return ESP_OK on success
     */
    esp_err_t read(SensorData& out_data);

private:
    i2c_port_t _port;
    uint8_t _bmp_addr;

    // BMP280 Calibration Data
    struct Bmp280CalibData {
        uint16_t dig_T1; int16_t dig_T2; int16_t dig_T3;
        uint16_t dig_P1; int16_t dig_P2; int16_t dig_P3; int16_t dig_P4;
        int16_t  dig_P5; int16_t dig_P6; int16_t dig_P7; int16_t dig_P8; int16_t dig_P9;
    } _calib;

    int32_t _t_fine;

    /**
     * Write one byte to a device register over I2C
     */
    esp_err_t write_reg(uint8_t addr, uint8_t reg, uint8_t data);
    
    /**
     * Read factory calibration coefficients from BMP280 NVM
     */
    esp_err_t bmp280_read_calib();

    /**
     * Apply Bosch calibration formula to raw temperature ADC value (from datasheet)
     */
    float bmp280_compensate_T(int32_t adc_T);

    /**
     * Apply Bosch calibration formula to raw pressure ADC value (from datasheet)
     */
    float bmp280_compensate_P(int32_t adc_P);
};