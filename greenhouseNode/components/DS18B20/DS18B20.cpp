#include "DS18B20.hpp"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"

static const char* TAG = "DS18B20";

// Standard Dallas 1-Wire commands
#define CMD_SKIP_ROM       0xCC
#define CMD_CONVERT_T      0x44
#define CMD_READ_SCRATCH   0xBE
#define CMD_WRITE_SCRATCH  0x4E

DS18B20::DS18B20(gpio_num_t dataPin) 
    : _dataPin(dataPin), _currentResolution(Resolution::RES_12_BIT) {
}

esp_err_t DS18B20::init() {
    // Configure the GPIO pin for 1-Wire communication
    gpio_config_t io_conf = {};
    io_conf.intr_type = GPIO_INTR_DISABLE;
    io_conf.mode = GPIO_MODE_INPUT_OUTPUT_OD; // Open-drain for 1-Wire
    io_conf.pin_bit_mask = (1ULL << _dataPin);
    io_conf.pull_down_en = GPIO_PULLDOWN_DISABLE;
    io_conf.pull_up_en = GPIO_PULLUP_ENABLE; // Requires external 4.7k resistor ideally
    
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
    // Implementation to write to the scratchpad to configure 9 to 12 bit resolution
    // ...
    return ESP_OK;
}

float DS18B20::readTemperatureC() {
    // Reset bus and send SKIP ROM (if only one device on bus)
    resetBus();
    writeByte(CMD_SKIP_ROM);
    
    // Send Convert Temperature command
    writeByte(CMD_CONVERT_T);
    
    // Wait for conversion. Max 750ms for 12-bit.
    vTaskDelay(pdMS_TO_TICKS(750)); 
    
    // Reset bus again, SKIP ROM, and Read Scratchpad
    resetBus();
    writeByte(CMD_SKIP_ROM);
    writeByte(CMD_READ_SCRATCH);
    
    // Read the 2 bytes of temperature data
    uint8_t lsb = readByte();
    uint8_t msb = readByte();
    
    // Placeholder calculation
    int16_t raw_temp = (msb << 8) | lsb;
    float tempC = (float)raw_temp / 16.0;
    
    // Enforce hardware limits: -55°C to +125°C
    if (tempC < -55.0 || tempC > 125.0) {
        ESP_LOGW(TAG, "Temperature out of measuring range!");
    }
    
    return tempC;
}

// --------------------------------------------------------
// 1-Wire Bit-Banging implementation details would go below
// (Using ESP-IDF standard esp_rom_delay_us for timing)
// --------------------------------------------------------
void DS18B20::resetBus() {
    // Pull the bus low for 480 microseconds to send the reset pulse
    gpio_set_level(_dataPin, 0);
    esp_rom_delay_us(480);

    // Release the bus (let it float high) and wait 70us 
    gpio_set_level(_dataPin, 1);
    esp_rom_delay_us(70);

    // Optional: At this point we could read the pin to check for a "presence pulse"
    // (a 0 means a sensor is connected and responding), but we will assume it's there.
    
    // Wait out the rest of the 480us time slot
    esp_rom_delay_us(410);
}

void DS18B20::writeBit(uint8_t bit) {
    portENTER_CRITICAL(&mux);
    if (bit & 1) {
        // Write '1' bit
        gpio_set_level(_dataPin, 0);
        esp_rom_delay_us(6);   // Pull low for a very short time
        gpio_set_level(_dataPin, 1);
        esp_rom_delay_us(64);  // Let it float high for the rest of the slot
    } else {
        // Write '0' bit
        gpio_set_level(_dataPin, 0);
        esp_rom_delay_us(60);  // Pull low for almost the whole slot
        gpio_set_level(_dataPin, 1);
        esp_rom_delay_us(10);  // Brief recovery time
    }
    portEXIT_CRITICAL(&mux);
}

uint8_t DS18B20::readBit() {
    uint8_t bit = 0;
    portENTER_CRITICAL(&mux);
    
    // Initiate the read time slot by pulling low for 6us
    gpio_set_level(_dataPin, 0);
    esp_rom_delay_us(6);
    
    // Release the bus and wait 9us to let the sensor pull it down if sending a 0
    gpio_set_level(_dataPin, 1);
    esp_rom_delay_us(9);
    
    // Sample the bus
    bit = gpio_get_level(_dataPin);
    
    // Wait out the remainder of the time slot
    esp_rom_delay_us(55);
    
    portEXIT_CRITICAL(&mux);
    return bit;
}

void DS18B20::writeByte(uint8_t data) {
    // 1-Wire transmits the Least Significant Bit (LSB) first
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