#pragma once
#include <stdint.h>

#define CMD_ACTUATOR_LEN 5

typedef struct {
    uint32_t node_id;
    char     actuator[CMD_ACTUATOR_LEN];  // CMD_ACTUATOR_LEN - 1 char for null terminator
    uint8_t  value;                        // 0 = off, 1-255=level (PWM)
    uint16_t duration_s;                   // 0 = hold indefinitely
} __attribute__((packed)) command_packet_t;
