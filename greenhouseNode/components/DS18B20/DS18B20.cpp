#include "DS18B20.hpp"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"

static const char* TAG = "DS18B20";

DS18B20::DS18B20(gpio_num_t dataPin) 
    : _dataPin(dataPin), _currentResolution(Resolution::RES_12_BIT) {
}

esp_err_t DS18B20::init() {
    gpio_config_t io_conf = {};
    io_conf.intr_type = GPIO_INTR_DISABLE;
    io_conf.mode = GPIO_MODE_INPUT_OUTPUT_OD;
    io_conf.pin_bit_mask = (1ULL << _dataPin);
    io_conf.pull_down_en = GPIO_PULLDOWN_DISABLE;
    io_conf.pull_up_en = GPIO_PULLUP_ENABLE;
    
    esp_err_t err = gpio_config(&io_conf);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to initialize GPIO for DS18B20");
        return err;
    }
    
    ESP_LOGI(TAG, "DS18B20 initialized on 1-Wire interface");
    return ESP_OK;
}

esp_err_t DS18B20::setResolution(Resolution res) {
    _currentResolution = res;

    resetBus();
    writeByte(static_cast<uint8_t>(Cmd::SKIP_ROM));
    writeByte(static_cast<uint8_t>(Cmd::WRITE_SCRATCH));
    writeByte(0x00);
    writeByte(0x00);
    writeByte(static_cast<uint8_t>(res));

    return ESP_OK;
}

float DS18B20::readTemperatureC() {
    resetBus();
    writeByte(static_cast<uint8_t>(Cmd::SKIP_ROM));

    writeByte(static_cast<uint8_t>(Cmd::CONVERT_T));

    vTaskDelay(pdMS_TO_TICKS(750));

    resetBus();
    writeByte(static_cast<uint8_t>(Cmd::SKIP_ROM));
    writeByte(static_cast<uint8_t>(Cmd::READ_SCRATCH));
    
    uint8_t lsb = readByte();
    uint8_t msb = readByte();
    
    int16_t raw_temp = (msb << 8) | lsb;
    float tempC = (float)raw_temp / 16.0;
    
    if (tempC < -55.0 || tempC > 125.0) {
        ESP_LOGW(TAG, "Temperature out of measuring range!");
    }
    
    return tempC;
}

void DS18B20::resetBus() {
    gpio_set_level(_dataPin, 0);
    esp_rom_delay_us(480);

    gpio_set_level(_dataPin, 1);
    esp_rom_delay_us(70);

    esp_rom_delay_us(410);
}

void DS18B20::writeBit(uint8_t bit) {
    portENTER_CRITICAL(&mux);
    if (bit & 1) {
        gpio_set_level(_dataPin, 0);
        esp_rom_delay_us(6);
        gpio_set_level(_dataPin, 1);
        esp_rom_delay_us(64);
    } else {
        gpio_set_level(_dataPin, 0);
        esp_rom_delay_us(60);
        gpio_set_level(_dataPin, 1);
        esp_rom_delay_us(10);
    }
    portEXIT_CRITICAL(&mux);
}

uint8_t DS18B20::readBit() {
    uint8_t bit = 0;
    portENTER_CRITICAL(&mux);
    
    gpio_set_level(_dataPin, 0);
    esp_rom_delay_us(6);
    
    gpio_set_level(_dataPin, 1);
    esp_rom_delay_us(9);
    
    bit = gpio_get_level(_dataPin);
    
    esp_rom_delay_us(55);
    
    portEXIT_CRITICAL(&mux);
    return bit;
}

void DS18B20::writeByte(uint8_t data) {
    for (int i = 0; i < 8; i++) {
        writeBit(data & 0x01);
        data >>= 1;
    }
}

uint8_t DS18B20::readByte() {
    uint8_t data = 0;
    for (int i = 0; i < 8; i++) {
        if (readBit()) {
            data |= (1 << i);
        }
    }
    return data;
}