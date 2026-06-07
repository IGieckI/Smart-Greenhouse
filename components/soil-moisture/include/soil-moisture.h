#pragma once

#include "esp_err.h"
#include "esp_adc/adc_oneshot.h"

// Opaque handle for the soil moisture sensor
typedef struct soil_moisture_t* soil_moisture_handle_t;

// Configuration structure for the sensor
typedef struct {
    adc_unit_t adc_unit;
    adc_channel_t adc_channel;
    int dry_value;              // ADC raw value when completely dry (air)
    int wet_value;              // ADC raw value when completely wet (water)
} soil_moisture_config_t;

/**
 * @brief Initialize the soil moisture sensor
 * * @param config Pointer to the configuration structure
 * @param handle Pointer to the handle to be created
 * @return esp_err_t ESP_OK on success
 */
esp_err_t soil_moisture_init(const soil_moisture_config_t *config, soil_moisture_handle_t *handle);

/**
 * @brief Read the raw ADC value from the sensor
 * * @param handle Sensor handle
 * @param raw_val Pointer to store the raw reading
 * @return esp_err_t ESP_OK on success
 */
esp_err_t soil_moisture_read_raw(soil_moisture_handle_t handle, int *raw_val);

/**
 * @brief Read the moisture as a percentage (0-100%)
 * * @param handle Sensor handle
 * @param percentage Pointer to store the percentage reading
 * @return esp_err_t ESP_OK on success
 */
esp_err_t soil_moisture_read_percentage(soil_moisture_handle_t handle, float *percentage);

/**
 * @brief Free the resources used by the sensor
 * * @param handle Sensor handle
 * @return esp_err_t ESP_OK on success
 */
esp_err_t soil_moisture_deinit(soil_moisture_handle_t handle);