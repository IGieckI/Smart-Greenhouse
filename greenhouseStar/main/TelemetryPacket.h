#include <cstdint>

typedef struct {
    uint8_t node_id;
    float water_temp;
    float tds_value;
    float soil_moisture;
    float light_lux;
    float air_temp;
    float humidity;
    float pressure;
} telemetry_packet_t;