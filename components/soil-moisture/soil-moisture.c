#include "soil_moisture.h"
#include "esp_log.h"
#include <stdlib.h>

static const char *TAG = "SOIL_MOISTURE";

// Internal structure to hold instance data
struct soil_moisture_t {
    adc_oneshot_unit_handle_t adc_handle;
    adc_channel_t adc_channel;
    int dry_value;
    int wet_value;
};

esp_err_t soil_moisture_init(const soil_moisture_config_t *config, soil_moisture_handle_t *out_handle) {
    if (!config || !out_handle) {
        return ESP_ERR_INVALID_ARG;
    }

    struct soil_moisture_t *sensor = calloc(1, sizeof(struct soil_moisture_t));
    if (!sensor) {
        return ESP_ERR_NO_MEM;
    }

    sensor->adc_channel = config->adc_channel;
    sensor->dry_value = config->dry_value;
    sensor->wet_value = config->wet_value;

    // Initialize the ADC Unit
    adc_oneshot_unit_init_cfg_t init_config = {
        .unit_id = config->adc_unit,
    };
    esp_err_t ret = adc_oneshot_new_unit(&init_config, &sensor->adc_handle);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to initialize ADC unit");
        free(sensor);
        return ret;
    }

    // Configure the ADC Channel
    adc_oneshot_chan_cfg_t chan_config = {
        .bitwidth = ADC_BITWIDTH_DEFAULT,
        .atten = ADC_ATTEN_DB_12, // 12dB attenuation allows reading up to ~3.1V
    };
    ret = adc_oneshot_config_channel(sensor->adc_handle, config->adc_channel, &chan_config);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to configure ADC channel");
        adc_oneshot_del_unit(sensor->adc_handle);
        free(sensor);
        return ret;
    }

    *out_handle = sensor;
    ESP_LOGI(TAG, "Soil moisture sensor initialized successfully on Unit %d, Channel %d", config->adc_unit, config->adc_channel);
    return ESP_OK;
}

esp_err_t soil_moisture_read_raw(soil_moisture_handle_t handle, int *raw_val) {
    if (!handle || !raw_val) return ESP_ERR_INVALID_ARG;
    return adc_oneshot_read(handle->adc_handle, handle->adc_channel, raw_val);
}

esp_err_t soil_moisture_read_percentage(soil_moisture_handle_t handle, float *percentage) {
    if (!handle || !percentage) return ESP_ERR_INVALID_ARG;

    int raw_val = 0;
    esp_err_t ret = adc_oneshot_read(handle->adc_handle, handle->adc_channel, &raw_val);
    if (ret != ESP_OK) return ret;

    // Map the raw value to a 0-100% scale
    // Usually, dry soil = higher voltage, wet soil = lower voltage (need to check)
    float mapped = 100.0f * (float)(handle->dry_value - raw_val) / (float)(handle->dry_value - handle->wet_value);
    
    if (mapped > 100.0f) mapped = 100.0f;
    if (mapped < 0.0f) mapped = 0.0f;

    *percentage = mapped;
    return ESP_OK;
}

esp_err_t soil_moisture_deinit(soil_moisture_handle_t handle) {
    if (!handle) return ESP_ERR_INVALID_ARG;
    
    esp_err_t ret = adc_oneshot_del_unit(handle->adc_handle);
    if (ret == ESP_OK) {
        free(handle);
    }
    return ret;
}