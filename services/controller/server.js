const express = require('express');
const fs = require('fs');
const { exec } = require('child_process');
const path = require('path');
const mqtt = require('mqtt');
const { InfluxDB, Point } = require('@influxdata/influxdb-client');

const app = express();
app.use(express.json());

const url = process.env.INFLUX_URL || 'http://localhost:8086';
const token = process.env.INFLUX_TOKEN;
const org = process.env.INFLUX_ORG;
const bucket = process.env.INFLUX_BUCKET;
const influxDB = new InfluxDB({ url, token });
const writeApi = influxDB.getWriteApi(org, bucket);

const LEAF_TEMP_LABEL = process.env.LEAF_TEMP_LABEL || 'leaf_temp';
const FALLBACK_FILE_PATH = './data/leaf_temp.txt';
const AUDIO_FILE = path.join(__dirname, 'ringtone.mp3'); 

const SAMPLING_FREQ_MIN = 6;
const ASSUMPTION_EQUALS = 2;
const TOLERANCE = 2; 
const FALLBACK_THR = (SAMPLING_FREQ_MIN * ASSUMPTION_EQUALS + TOLERANCE);

const NUMERIC_FIELDS = ["air_temp", "humidity", "pressure", "water_temp", "soil_moisture", "tds", "light_lux", LEAF_TEMP_LABEL];

// Node-to-Star topology: { "<node_id>": "<star_id>" }, persisted across restarts
const TOPOLOGY_FILE = './data/topology.json';
let topology = {};
try {
    if (fs.existsSync(TOPOLOGY_FILE)) {
        topology = JSON.parse(fs.readFileSync(TOPOLOGY_FILE, 'utf8'));
    }
} catch (e) {
    console.error('[Controller] Could not load topology file:', e.message);
}
function saveTopology() {
    try { fs.writeFileSync(TOPOLOGY_FILE, JSON.stringify(topology, null, 2)); }
    catch (e) { console.error('[Controller] Could not save topology file:', e.message); }
}

// MQTT client used to publish actuation commands and receive ACKs
const mqttClient = mqtt.connect('mqtt://mosquitto:1883');
mqttClient.on('connect', () => {
    console.log('[Controller] MQTT connected to mosquitto');
    mqttClient.subscribe('greenhouse/acks', { qos: 1 });
});
mqttClient.on('error', (e) => console.error('[Controller] MQTT error:', e.message));

// Pending commands awaiting ACK: node_id (string) → { star_id, payload, attempts, timer }
const pendingCommands = {};
const COMMAND_TIMEOUT_MS = 3000;
const COMMAND_MAX_ATTEMPTS = 3;

function publishCommand(node_id, star_id, payload, attempt) {
    const topic = `greenhouse/commands/${star_id}`;
    mqttClient.publish(topic, payload, { qos: 1 });
    console.log(`[Controller] Command -> ${topic} (attempt ${attempt}/${COMMAND_MAX_ATTEMPTS}): ${payload}`);
}

function scheduleRetry(node_id) {
    const key = String(node_id);
    const pending = pendingCommands[key];
    if (!pending) return;
    pending.timer = setTimeout(() => {
        if (!pendingCommands[key]) return;
        if (pending.attempts >= COMMAND_MAX_ATTEMPTS) {
            console.error(`[Controller] Command to node ${node_id} unacknowledged after ${COMMAND_MAX_ATTEMPTS} attempts`);
            delete pendingCommands[key];
            return;
        }
        pending.attempts++;
        publishCommand(node_id, pending.star_id, pending.payload, pending.attempts);
        scheduleRetry(node_id);
    }, COMMAND_TIMEOUT_MS);
}

mqttClient.on('message', (topic, message) => {
    if (topic !== 'greenhouse/acks') return;
    try {
        const data = JSON.parse(message.toString());
        if (data.ack && data.nid !== undefined) {
            const key = String(data.nid);
            if (pendingCommands[key]) {
                clearTimeout(pendingCommands[key].timer);
                delete pendingCommands[key];
                console.log(`[Controller] ACK received for node ${key}`);
            }
        }
    } catch (_) {}
});

// Variabili globali per tracciare l'utilizzo degli ID
let fallbackUsageCount = {};
let currentFallbackId = null;


const delay = (ms) => new Promise(resolve => setTimeout(resolve, ms));

/**
 * Emits N audio notifications spaced 400ms apart, usefull to have a sonoric feedback
 * @param {number} times - Number of playback iterations
 */
const playBeeps = async (times) => {
    for (let i = 0; i < times; i++) {
        await new Promise((resolve) => {
            exec(`mpg123 ${AUDIO_FILE}`, (error) => {
                if (error) {
                    console.error(`Playback error: ${error.message}`);
                }
                resolve();
            });
        });
        
        await delay(400); 
    }
};



app.post('/api/data', async (req, res) => {
    let data = req.body;

    console.log(data)

    if (!data.node_id) {
        return res.status(400).send({ error: "Missing node_id" });
    }

    // Keep topology file up-to-date so commands can be routed to the right Star
    if (data.star_id) {
        const key = String(data.node_id);
        if (topology[key] !== String(data.star_id)) {
            topology[key] = String(data.star_id);
            saveTopology();
            console.log(`[Controller] Topology: node ${data.node_id} -> star ${data.star_id}`);
        }
    }

    // Gestione File TXT per Temperatura Fogliare
    if ((data[LEAF_TEMP_LABEL] === undefined) || (data[LEAF_TEMP_LABEL] < 5.0)) {
        try {
            if (fs.existsSync(FALLBACK_FILE_PATH)) {
                const fileContent = fs.readFileSync(FALLBACK_FILE_PATH, 'utf8').trim(); 
                const parts = fileContent.split('/');
                
                if (parts.length === 2) {
                    const tempVal = parseFloat(parts[0].replace(',', '.'));
                    const tempId = parts[1];

                    // Reset counter if a new ID transition is detected in the fallback schema
                    if (currentFallbackId !== tempId) {
                        currentFallbackId = tempId;
                        fallbackUsageCount[tempId] = 0; 
                    }

                    // Strict Usage Threshold Validation
                    if (fallbackUsageCount[tempId] < FALLBACK_THR) {
                        data[LEAF_TEMP_LABEL] = tempVal;

                        const currentCount = fallbackUsageCount[tempId];
                        fallbackUsageCount[tempId]++; 
                        
                        console.log(`[Controller] Leaf temp read: ${tempVal}°C (ID: ${tempId}, Usage: ${currentCount + 1}/${FALLBACK_THR})`);
                        
                        if (currentCount % SAMPLING_FREQ_MIN === 0) {
                            const numBeeps = (currentCount / SAMPLING_FREQ_MIN) + 1;
                            playBeeps(numBeeps);
                        }
                    } else {
                        console.log(`[Controller] WARNING: ID '${tempId}' has been used ${FALLBACK_THR} times. Leaf data DISCARDED. Update the txt file!`);
                        // CRITICAL EMERGENCY ALARM: 5 sequential notice alerts
                        playBeeps(5);
                    }
                } else {
                    console.error("[Controller] Invalid txt format. Use layout: 21.5/A");
                    data[LEAF_TEMP_LABEL] = undefined
                }
            }
        } catch (error) {
            console.error(`[Controller] Fallback file read error: ${error.message}`);
        }
    }

    // Dynamic Database Pipeline Execution
    try {
        const point = new Point('sensor_measurements').tag('id_board', String(data.node_id));
            
        // Map available numerical fields dynamically from payload
        for (const field of NUMERIC_FIELDS) {
            if (data[field] !== undefined && !isNaN(data[field])) {
                point.floatField(field, Number(data[field]));
            }
        }

        console.log(point);

        writeApi.writePoint(point);
        await writeApi.flush(); 

        console.log(`[Controller] Data successfully written to Influx for board: ${data.node_id}`);
        res.status(200).send({ status: "success" });
    } catch (error) {
        console.error("[Controller] Influx write error:", error);
        res.status(500).send({ error: "Database error" });
    }
});

// Send an actuation command to a Node.
// The Star managing that node is looked up from the topology file.
// Body: { node_id: number, actuator: string, value: number (0-255), duration_s: number }
app.post('/api/command', (req, res) => {
    const { node_id, actuator, value, duration_s } = req.body;

    if (!node_id || !actuator || value === undefined) {
        return res.status(400).send({ error: 'node_id, actuator, value required' });
    }

    const star_id = topology[String(node_id)];
    if (!star_id) {
        return res.status(404).send({
            error: `No star known for node_id ${node_id}. Wait for the node to send at least one telemetry packet.`
        });
    }

    const payload = JSON.stringify({
        nid: Number(node_id),
        act: String(actuator),
        val: Number(value),
        dur: Number(duration_s) || 0
    });

    // Cancel any in-flight command for this node before issuing a new one
    const key = String(node_id);
    if (pendingCommands[key]) clearTimeout(pendingCommands[key].timer);
    pendingCommands[key] = { star_id, payload, attempts: 1, timer: null };

    publishCommand(node_id, star_id, payload, 1);
    scheduleRetry(node_id);

    res.status(200).send({ status: 'sent', star_id, node_id, topic: `greenhouse/commands/${star_id}` });
});

const PORT = process.env.PORT || 3001;
app.listen(PORT, () => {
    console.log(`Controller listening on port ${PORT}`);
});