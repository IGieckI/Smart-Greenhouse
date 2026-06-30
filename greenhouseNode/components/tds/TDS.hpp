#pragma once

#include <esp_err.h>
#include <esp_adc/adc_oneshot.h>
#include <esp_adc/adc_cali.h>

/**
 * @ref Based on DFRobot/DFRobot_ESP_EC_and_TDS_with_MQTT_Iotcloud implementation.
 */
class TDS {
public:
    /**
     * TDS Constructor
     * @param adc_handle Initialized ADC oneshot unit handle
     * @param channel ADC channel the sensor is connected to
     * @param cali_handle (Optional) ADC calibration handle for accurate voltage reading
     */
    TDS(adc_oneshot_unit_handle_t adc_handle, adc_channel_t channel, adc_cali_handle_t cali_handle = nullptr);

    /**
     * @brief Set the ambient temperature for compensation
     * @param temp Temperature in Celsius
     */
    void setTemperature(float temp);

    /**
     * @brief Set the calibration K-Value
     * @param k Calibration factor
     */
    void setKValue(float k);

    /**
     * @brief Get the current calibration K-Value
     * @return float 
     */
    float getKValue() const;

    /**
     * @brief Read the ADC and calculate the new TDS value
     * @return esp_err_t ESP_OK on success
     */
    esp_err_t update();

    /**
     * @brief Get the last calculated TDS value
     * @return float TDS value in ppm
     */
    float getTdsValue() const;

private:
    adc_oneshot_unit_handle_t _adc_handle;
    adc_channel_t _channel;
    adc_cali_handle_t _cali_handle;
    
    float _temperature;
    float _kValue;
    float _tdsValue;
};