#pragma once

#include "driver/gpio.h"
#include "driver/i2c.h"
#include "hal/adc_types.h"

// --- Soil Moisture Sensor Configuration ---
#define SOIL_MOISTURE_ADC_UNIT    ADC_UNIT_1
#define SOIL_MOISTURE_ADC_CHANNEL ADC_CHANNEL_5
#define SOIL_MOISTURE_DRY_VAL     4095
#define SOIL_MOISTURE_WET_VAL     960

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

// Network stuff
#define NODE_ID 1
#define XSTR(x) STR(x)
#define STR(x) #x
static uint8_t central_mac[6] = {0x3C, 0x0F, 0x02, 0xEB, 0x8A, 0x5C};