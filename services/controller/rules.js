const mqttService = require('./mqtt');
const config = require('./config');

const SOIL_MOISTURE_THRESHOLD = 30.0; 

const rules = [
    {
        id: 'low_soil_moisture_pump_activation',
        evaluate: (nodeId, data) => {
            return data.soil_moisture !== undefined && data.soil_moisture < SOIL_MOISTURE_THRESHOLD;
        },
        execute: (nodeId, data) => {
            const actuator = "pump"; 
            const value = 255;       
            const duration_s = 10;   
            try {
                console.log(`${config.MY_TAG} Rule Triggered: Soil moisture low (${data.soil_moisture}). Turning on pump for node ${nodeId}`);
                mqttService.sendCommand(nodeId, actuator, value, duration_s);
            } catch (e) {
                console.error(`${config.MY_TAG} Failed to execute rule 'low_soil_moisture': ${e.message}`);
            }
        }
    }
];

function applyRules(nodeId, data) {
    for (const rule of rules) {
        try {
            if (rule.evaluate(nodeId, data)) {
                rule.execute(nodeId, data);
            }
        } catch (e) {
            console.error(`${config.MY_TAG} Error evaluating rule ${rule.id}:`, e.message);
        }
    }
}

module.exports = { applyRules };