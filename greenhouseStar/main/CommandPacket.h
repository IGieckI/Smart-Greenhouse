#pragma once
#include <stdint.h>

#define CMD_ACTUATOR_LEN 5

typedef struct {
    uint32_t node_id;
    char     actuator[CMD_ACTUATOR_LEN];  // null-terminated name, e.g. "pump", "led"
    uint8_t  value;                        // 0=off, 1-255=level (binary: 0 or non-zero; PWM: duty mapped to full range)
    uint16_t duration_s;                   // 0=hold indefinitely
} __attribute__((packed)) command_packet_t;
