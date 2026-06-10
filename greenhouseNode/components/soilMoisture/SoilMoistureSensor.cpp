#include "SoilMoistureSensor.hpp"
#include "esp_log.h"
#include <algorithm>

static const char* TAG = "SOIL_MOISTURE";

SoilMoistureSensor::SoilMoistureSensor()
    : _adc_handle(nullptr),
      _adc_channel(ADC_CHANNEL_0),
      _dry_value(0),
      _wet_value(0),
      _is_initialized(false) {
}

SoilMoistureSensor::~SoilMoistureSensor() {
    // Replaces soil_moisture_deinit()
    if (_is_initialized && _adc_handle) {
        adc_oneshot_del_unit(_adc_handle);
        ESP_LOGI(TAG, "Soil moisture sensor deinitialized");
    }
}

esp_err_t SoilMoistureSensor::init(const Config& config) {
    if (_is_initialized) {
        ESP_LOGW(TAG, "Sensor is already initialized");
        return ESP_ERR_INVALID_STATE;
    }
    
    if (config.adc_handle == nullptr) {
        ESP_LOGE(TAG, "ADC handle passed in config is null!");
        return ESP_ERR_INVALID_ARG;
    }

    _adc_handle = config.adc_handle;
    _adc_channel = config.adc_channel;
    _dry_value = config.dry_value;
    _wet_value = config.wet_value;

    adc_oneshot_chan_cfg_t chan_config = {};
    chan_config.bitwidth = ADC_BITWIDTH_DEFAULT;
    chan_config.atten = ADC_ATTEN_DB_12; 

    esp_err_t ret = adc_oneshot_config_channel(_adc_handle, _adc_channel, &chan_config);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to configure ADC channel");
        return ret;
    }

    _is_initialized = true;
    ESP_LOGI(TAG, "Soil moisture sensor initialized successfully on Channel %d", config.adc_channel);
    return ESP_OK;
}

esp_err_t SoilMoistureSensor::read_raw(int& raw_val) {
    if (!_is_initialized) return ESP_ERR_INVALID_STATE;
    return adc_oneshot_read(_adc_handle, _adc_channel, &raw_val);
}

esp_err_t SoilMoistureSensor::read_percentage(float& percentage) {
    if (!_is_initialized) return ESP_ERR_INVALID_STATE;

    int raw_val = 0;
    esp_err_t ret = adc_oneshot_read(_adc_handle, _adc_channel, &raw_val);
    ESP_LOGI("MAIN", "Raw Moisture Value: %d", raw_val);
    if (ret != ESP_OK) return ret;

    // Map the raw value to a 0-100% scale
    float mapped = 100.0f * static_cast<float>(_dry_value - raw_val) / static_cast<float>(_dry_value - _wet_value);
    
    // std::clamp safely restricts the mapped value between 0.0f and 100.0f
    percentage = std::clamp(mapped, 0.0f, 100.0f);

    return ESP_OK;
}