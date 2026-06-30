#include "TDS.hpp"
#include <cmath>
#include "esp_log.h"

static const char* TAG = "TDS";

TDS::TDS(adc_oneshot_unit_handle_t adc_handle, adc_channel_t channel, adc_cali_handle_t cali_handle)
    : _adc_handle(adc_handle), 
      _channel(channel), 
      _cali_handle(cali_handle), 
      _temperature(25.0f), 
      _kValue(1.0f), 
      _tdsValue(0.0f) 
{
}

void TDS::setTemperature(float temp) {
    _temperature = temp;
}

void TDS::setKValue(float k) {
    _kValue = k;
}

float TDS::getKValue() const {
    return _kValue;
}

esp_err_t TDS::update() {
    if (!_adc_handle) {
        ESP_LOGE(TAG, "ADC handle is null");
        return ESP_ERR_INVALID_ARG;
    }

    int raw_val = 0;
    esp_err_t err = adc_oneshot_read(_adc_handle, _channel, &raw_val);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "ADC read failed: %s", esp_err_to_name(err));
        return err;
    }

    float voltage = 0.0f;
    
    if (_cali_handle) {
        int voltage_mv = 0;
        adc_cali_raw_to_voltage(_cali_handle, raw_val, &voltage_mv);
        voltage = voltage_mv / 1000.0f;
    } else {
        voltage = (raw_val / 4095.0f) * 3.3f;
    }

    float compensationCoefficient = 1.0f + 0.02f * (_temperature - 25.0f);
    float compensationVoltage = voltage / compensationCoefficient;

    _tdsValue = (133.42f * std::pow(compensationVoltage, 3) -
                 255.86f * std::pow(compensationVoltage, 2) +
                 857.39f * compensationVoltage) * 0.5f * _kValue;

    return ESP_OK;
}

float TDS::getTdsValue() const {
    return _tdsValue;
}