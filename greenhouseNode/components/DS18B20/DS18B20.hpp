#pragma once

#include "driver/gpio.h"
#include "esp_err.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

/**
 * @ref Based on milesburton/Arduino-Temperature-Control-Library implementation
 */
class DS18B20 {
public:
    /**
     * @brief The DS18B20 provides 9 to 12 bit configurable temperature readings each of them with a precision/time trade-off.
     */
    enum class Resolution : uint8_t {
        RES_9_BIT  = 0x1F,
        RES_10_BIT = 0x3F,
        RES_11_BIT = 0x5F,
        RES_12_BIT = 0x7F,
    };

    /**
     * @brief Constructor for the DS18B20.
     * @param dataPin The GPIO pin connected to the yellow data wire.
     */
    DS18B20(gpio_num_t dataPin);

    /**
     * @brief Initializes the 1-Wire interface bus.
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
     * @return float Temperature in Celsius (from -55°C to +125°C).
     */
    float readTemperatureC();

private:
    gpio_num_t _dataPin;
    Resolution _currentResolution;

    enum class Cmd : uint8_t {
        SKIP_ROM      = 0xCC,
        CONVERT_T     = 0x44,
        READ_SCRATCH  = 0xBE,
        WRITE_SCRATCH = 0x4E,
    };

    // spinlock for 1-Wire critical sections
    portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
    
    /**
     * Send 1-Wire reset pulse and detect presence
     */
    void resetBus();
    
    /**
     * Write a single bit onto the bus
     */
    void writeBit(uint8_t bit);
    
    /**
     * Read a single bit from the bus
     */
    uint8_t readBit();
    
    /**
     * Write 8 bits LSB-first
     */
    void writeByte(uint8_t data);
    
    /**
     * Read 8 bits LSB-first
     */
    uint8_t readByte();
};