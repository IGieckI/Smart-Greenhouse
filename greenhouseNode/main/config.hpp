#pragma once

#include "driver/gpio.h"
#include "driver/i2c.h"
#include "hal/adc_types.h"

// --- Soil Moisture Sensor Configuration ---
#define SOIL_MOISTURE_ADC_UNIT    ADC_UNIT_2 
#define SOIL_MOISTURE_ADC_CHANNEL ADC_CHANNEL_8
#define SOIL_MOISTURE_DRY_VAL     4095
#define SOIL_MOISTURE_WET_VAL     1500

// --- TDS Sensor Configuration ---
#define TDS_ADC_UNIT        ADC_UNIT_1
#define TDS_ADC_CHANNEL     ADC_CHANNEL_0

// --- DS18B20 Temperature Sensor Configuration ---
#define DS18B20_DATA_PIN    GPIO_NUM_32

// --- BH1750 Light Sensor (I2C) Configuration ---
#define I2C_MASTER_NUM      I2C_NUM_0
#define I2C_MASTER_SDA_IO   21
#define I2C_MASTER_SCL_IO   22
#define I2C_MASTER_FREQ_HZ  400000

// --- Main Loop Configuration ---
#define LOOP_DELAY_MS       1000