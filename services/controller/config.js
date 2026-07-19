const path = require('path');

module.exports = {
    PORT: process.env.PORT || 3001,
    INFLUX_URL: process.env.INFLUX_URL || 'http://localhost:8086',
    INFLUX_TOKEN: process.env.INFLUX_TOKEN,
    INFLUX_ORG: process.env.INFLUX_ORG,
    INFLUX_BUCKET: process.env.INFLUX_BUCKET,
    MQTT_URL: process.env.MQTT_URL || 'mqtt://mosquitto:1883',
    LEAF_TEMP_LABEL: process.env.LEAF_TEMP_LABEL || 'leaf_temp',
    NUMERIC_FIELDS: [
        "air_temp", "humidity", "pressure", "water_temp", 
        "soil_moisture", "tds", "light_lux", 
        process.env.LEAF_TEMP_LABEL || 'leaf_temp'
    ],
    FALLBACK_FILE_PATH: path.join(__dirname, 'data', 'leaf_temp.txt'),
    AUDIO_FILE: path.join(__dirname, 'ringtone.mp3'),
    TOPOLOGY_FILE: path.join(__dirname, 'data', 'topology.json'),
    SAMPLING_FREQ_MIN: 6,
    ASSUMPTION_EQUALS: 2,
    TOLERANCE: 2,
    get FALLBACK_THR() { 
        return this.SAMPLING_FREQ_MIN * this.ASSUMPTION_EQUALS + this.TOLERANCE; 
    },
    COMMAND_TIMEOUT_MS: 3000,
    COMMAND_MAX_ATTEMPTS: 3,
    MAX_COMMAND_DURATION_S: 300,


    SOIL_MOISTURE_LOWER_THRESHOLD: 60.0,
    PUMP_PATIENCE_COUNT: 5,
    
    MY_TAG: "[Controller]"
};