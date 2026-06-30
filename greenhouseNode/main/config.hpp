#pragma once
#include "driver/gpio.h"
#include "driver/i2c.h"
#include "driver/ledc.h"
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

#define PIN_PUMP GPIO_NUM_2

// Network stuff
// Star node MAC defined in personal_config.hpp

// --- Actuator table ---
// Names and types are fixed across all nodes. Pin assignments come from personal_config.hpp.
typedef enum { ACT_BINARY = 0, ACT_PWM = 1 } act_type_t;

typedef struct {
    const char     *name;
    gpio_num_t      pin;
    act_type_t      type;
    ledc_channel_t  ledc_ch;  // used only when type == ACT_PWM
} actuator_cfg_t;

static const actuator_cfg_t ACTUATOR_TABLE[] = {
    { "pump", PIN_PUMP, ACT_BINARY, LEDC_CHANNEL_0 },
};
#define ACTUATOR_COUNT (sizeof(ACTUATOR_TABLE) / sizeof(ACTUATOR_TABLE[0]))