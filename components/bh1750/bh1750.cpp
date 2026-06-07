#include "BH1750.hpp"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

BH1750::BH1750(i2c_port_t port, uint8_t addr) : _port(port), _addr(addr) {
    
}

esp_err_t BH1750::write_cmd(uint8_t cmd) {
    return i2c_master_write_to_device(_port, _addr, &cmd, 1, portMAX_DELAY);
}

esp_err_t BH1750::power_down() {
    return write_cmd(CMD_POWER_DOWN);
}

esp_err_t BH1750::power_on() {
    return write_cmd(CMD_POWER_ON);
}

esp_err_t BH1750::reset() {
    // Reset command is not acceptable in Power Down mode, so power on first
    esp_err_t err = power_on();
    if (err != ESP_OK) return err;
    
    return write_cmd(CMD_RESET);
}

esp_err_t BH1750::setup_mode(Mode mode) {
    // Cast the strongly-typed enum class to uint8_t for the I2C transfer
    esp_err_t err = write_cmd(static_cast<uint8_t>(mode));
    if (err != ESP_OK) return err;

    // Apply the maximum wait times defined in the datasheet
    if (mode == Mode::CONTINUOUS_L_RES || mode == Mode::ONE_TIME_L_RES) {
        vTaskDelay(pdMS_TO_TICKS(24));  // Max 24ms for L-Resolution
    } else {
        vTaskDelay(pdMS_TO_TICKS(180)); // Max 180ms for H-Resolution
    }
    
    return ESP_OK;
}

esp_err_t BH1750::read_lux(Mode mode, float &lux) {
    uint8_t data[2] = {0};
    
    esp_err_t err = i2c_master_read_from_device(_port, _addr, data, 2, portMAX_DELAY);
    if (err != ESP_OK) return err;

    // Combine High and Low bytes
    uint16_t raw_val = (data[0] << 8) | data[1];

    // Calculate lux based on the resolution mode
    if (mode == Mode::CONTINUOUS_H_RES2 || mode == Mode::ONE_TIME_H_RES2) {
        // H-Resolution Mode 2 is half the value
        lux = static_cast<float>(raw_val) / 2.4f; 
    } else {
        // Standard H-Resolution and L-Resolution
        lux = static_cast<float>(raw_val) / 1.2f; 
    }

    return ESP_OK;
}