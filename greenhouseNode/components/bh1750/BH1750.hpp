#pragma once

#include "driver/i2c.h"
#include "esp_err.h"

/**
 * @ref Based on claws/BH1750 implementation
 */
class BH1750 {
public:
    static constexpr uint8_t I2C_ADDRESS_LO = 0x23;

    /**
     * @brief Opcodes for measurement modes:
     *        CONTINUOUS_H_RES  - Continuous high resolution mode (1 lx resolution, typ. 120ms)
     *        CONTINUOUS_H_RES2 - Continuous high resolution mode 2 (0.5 lx resolution, typ. 120ms)
     *        CONTINUOUS_L_RES  - Continuous low resolution mode (4 lx resolution, typ. 16ms)
     *        ONE_TIME_H_RES    - One time high resolution mode (1 lx resolution, auto power down)
     *        ONE_TIME_H_RES2   - One time high resolution mode 2 (0.5 lx resolution, auto power down)
     *        ONE_TIME_L_RES    - One time low resolution mode (4 lx resolution, auto power down)
     */
    enum class Mode : uint8_t {
        CONTINUOUS_H_RES  = 0x10,
        CONTINUOUS_H_RES2 = 0x11,
        CONTINUOUS_L_RES  = 0x13,
        ONE_TIME_H_RES    = 0x20,
        ONE_TIME_H_RES2   = 0x21,
        ONE_TIME_L_RES    = 0x23
    };

    /**
     * @brief Construct a new BH1750 object
     * @param port I2C Port number
     * @param addr I2C address of the device
     */
    BH1750(i2c_port_t port, uint8_t addr = I2C_ADDRESS_LO);

    /**
     * @brief Power down the sensor (No active state)
     */
    esp_err_t power_down();

    /**
     * @brief Power on the sensor
     */
    esp_err_t power_on();

    /**
     * @brief Reset the data register (useful if switching modes)
     */
    esp_err_t reset();

    /**
     * @brief Configure the measurement mode and wait for the conversion to complete
     */
    esp_err_t setup_mode(Mode mode);

    /**
     * @brief Read the ambient light value in lux
     */
    esp_err_t read_lux(Mode mode, float &lux);

private:
    i2c_port_t _port;
    uint8_t _addr;

    static constexpr uint8_t CMD_POWER_DOWN = 0x00;
    static constexpr uint8_t CMD_POWER_ON   = 0x01;
    static constexpr uint8_t CMD_RESET      = 0x07;

    /** 
     * @brief Write a single opcode byte to the device over I2C 
     */
    esp_err_t write_cmd(uint8_t cmd);
};