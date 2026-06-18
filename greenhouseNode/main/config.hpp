#pragma once
#include "driver/gpio.h"
#include "driver/i2c.h"
#include "hal/adc_types.h"

// Soil Moisture Sensor
#define SOIL_MOISTURE_ADC_UNIT    ADC_UNIT_1
#define SOIL_MOISTURE_ADC_CHANNEL ADC_CHANNEL_5
#define SOIL_MOISTURE_DRY_VAL     1400
#define SOIL_MOISTURE_WET_VAL     960

// TDS Sensor
#define TDS_ADC_UNIT        ADC_UNIT_1
#define TDS_ADC_CHANNEL     ADC_CHANNEL_0

// DS18B20 Temperature Sensor
#define DS18B20_DATA_PIN    GPIO_NUM_32

// BH1750 Light Sensor (I2C)
#define I2C_MASTER_NUM      I2C_NUM_0
#define I2C_MASTER_SDA_IO   21
#define I2C_MASTER_SCL_IO   22
#define I2C_MASTER_FREQ_HZ  400000

// ADS1115 converter for leaf temperature
#define ADS1115_ADDR                0x48 
#define ADS_POINTER_CONVERSION      0x00
#define ADS_POINTER_CONFIG          0x01

// Main Loop
#define NUM_SAMPLES          5
#define SAMPLE_DELAY_MS      100
#define DEEP_SLEEP_MS      1000 // 300000

// Network stuff
static uint8_t central_mac[6] = {0x3C, 0x0F, 0x02, 0xEB, 0x8A, 0x5C};