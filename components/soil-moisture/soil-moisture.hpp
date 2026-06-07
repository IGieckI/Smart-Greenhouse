#pragma once

#include "esp_err.h"
#include "esp_adc/adc_oneshot.h"

class SoilMoistureSensor {
public:
    // Configuration structure mapped directly inside the class
    struct Config {
        adc_unit_t adc_unit;
        adc_channel_t adc_channel;
        int dry_value;              // ADC raw value when completely dry (air)
        int wet_value;              // ADC raw value when completely wet (water)
    };

    SoilMoistureSensor();
    
    // Destructor automatically cleans up the ADC unit
    ~SoilMoistureSensor();

    // Prevent copying to avoid multiple objects trying to free the same hardware handle
    SoilMoistureSensor(const SoilMoistureSensor&) = delete;
    SoilMoistureSensor& operator=(const SoilMoistureSensor&) = delete;

    /**
     * @brief Initialize the soil moisture sensor
     * @param config Configuration structure
     * @return esp_err_t ESP_OK on success
     */
    esp_err_t init(const Config& config);

    /**
     * @brief Read the raw ADC value from the sensor
     * @param raw_val Reference to store the raw reading
     * @return esp_err_t ESP_OK on success
     */
    esp_err_t read_raw(int& raw_val);

    /**
     * @brief Read the moisture as a percentage (0-100%)
     * @param percentage Reference to store the percentage reading
     * @return esp_err_t ESP_OK on success
     */
    esp_err_t read_percentage(float& percentage);

private:
    adc_oneshot_unit_handle_t _adc_handle;
    adc_channel_t _adc_channel;
    int _dry_value;
    int _wet_value;
    bool _is_initialized;
};