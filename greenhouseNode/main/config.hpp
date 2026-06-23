#pragma once
#include "driver/gpio.h"
#include "driver/i2c.h"
#include "hal/adc_types.h"

#include "personal_config.hpp"

// --- Soil Moisture Sensor Configuration ---
// Soil Moisture Sensor
#define SOIL_MOISTURE_ADC_UNIT    ADC_UNIT_1
#define SOIL_MOISTURE_ADC_CHANNEL ADC_CHANNEL_5

// TDS Sensor
#define TDS_ADC_UNIT        ADC_UNIT_1
#define TDS_ADC_CHANNEL     ADC_CHANNEL_0

// --- DS18B20 Temperature Sensor Configuration ---
// in personal_config.hpp

// BH1750 Light Sensor (I2C)
#define I2C_MASTER_NUM      I2C_NUM_0
#define I2C_MASTER_SDA_IO   21
#define I2C_MASTER_SCL_IO   22
#define I2C_MASTER_FREQ_HZ  400000

// Main Loop
#define NUM_SAMPLES          5
#define SAMPLE_DELAY_MS      100
#define DEEP_SLEEP_MS        120000
//120000

// Network stuff
// Star node MAC defined in personal_config.hpp