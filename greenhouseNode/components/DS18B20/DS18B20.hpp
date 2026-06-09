#pragma once

#include "driver/gpio.h"
#include "esp_err.h"
#include "esp_rom_sys.h"

class DS18B20 {
public:
    /**
     * @brief The DS18B20 provides 9 to 12 bit configurable temperature readings.
     */
    enum class Resolution {
        RES_9_BIT = 9,
        RES_10_BIT = 10,
        RES_11_BIT = 11,
        RES_12_BIT = 12
    };

    /**
     * @brief Constructor for the DS18B20 digital thermo probe[cite: 5].
     * @param dataPin The GPIO pin connected to the Yellow DATA wire.
     */
    DS18B20(gpio_num_t dataPin);

    /**
     * @brief Initializes the 1-Wire interface bus[cite: 6].
     * @return esp_err_t ESP_OK on success.
     */
    esp_err_t init();

    /**
     * @brief Sets the internal resolution of the sensor.
     * @param res Resolution enum (9 to 12 bits).
     * @return esp_err_t ESP_OK on success.
     */
    esp_err_t setResolution(Resolution res);

    /**
     * @brief Converts and reads the temperature.
     * Conversion to a 12-bit digital word takes up to 750ms (max)[cite: 7].
     * @return float Temperature in Celsius. Returns a specific error value if out of bounds 
     * (Operating range: -55°C to +125°C) [cite: 8].
     */
    float readTemperatureC();

private:
    gpio_num_t _dataPin;
    Resolution _currentResolution;
    
    // Internal helpers for 1-Wire protocol would go here
    void resetBus();
    void writeBit(uint8_t bit);
    uint8_t readBit();
    void writeByte(uint8_t data);
    uint8_t readByte();
};