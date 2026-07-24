const mqttService = require('./mqtt');
const config = require('./config');

const lowMoistureCounters = {};

const rules = [
    {
        id: 'low_soil_moisture_pump_activation',
        evaluate: (nodeId, data) => {
            console.log(`Init \"low_soil_moisture_pump_activation\" eval for ${nodeId}: data -> ${data.soil_moisture }`)
            if (data.soil_moisture === undefined) {
                return false;
            }

            if (lowMoistureCounters[nodeId] === undefined) {
                lowMoistureCounters[nodeId] = 0;
            }

            if (data.soil_moisture < config.SOIL_MOISTURE_LOWER_THRESHOLD) {
                lowMoistureCounters[nodeId]++;
                console.log("Warning: soil moisture value is low")
                console.log("Actual patience level:", lowMoistureCounters[nodeId])
                
                if (lowMoistureCounters[nodeId] >= config.PUMP_PATIENCE_COUNT) {
                    lowMoistureCounters[nodeId] = 0;
                    return true;
                }
            } else {
                console.log("No allarm: resetting patience")
                lowMoistureCounters[nodeId] = 0;
                console.log("Actual patience level:", lowMoistureCounters[nodeId])
            }

            return false;
        },
        execute: (nodeId, data) => {
            const actuator = "pump"; 
            const value = 255;       
            const duration_s = 60;   
            try {
                console.log(`${config.MY_TAG} Rule Triggered: Soil moisture too low (under ${config.SOIL_MOISTURE_LOWER_THRESHOLD}) too much time (max patience: ${config.PUMP_PATIENCE_COUNT}).`);
                console.log(`Turning on pump for node ${nodeId} for ${duration_s}`);
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