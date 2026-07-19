const mqtt = require('mqtt');
const config = require('./config');
const topologyService = require('./topology');

const mqttClient = mqtt.connect(config.MQTT_URL);
const pendingCommands = {};

mqttClient.on('connect', () => {
    console.log(`${config.MY_TAG} MQTT connected to mosquitto`);
    mqttClient.subscribe('greenhouse/acks', { qos: 1 });
});

mqttClient.on('error', (e) => console.error(`${config.MY_TAG} MQTT error:`, e.message));

mqttClient.on('message', (topic, message) => {
    if (topic !== 'greenhouse/acks') return;
    try {
        const data = JSON.parse(message.toString());
        if (data.ack && data.nid !== undefined) {
            const key = String(data.nid);
            if (pendingCommands[key]) {
                clearTimeout(pendingCommands[key].timer);
                delete pendingCommands[key];
                console.log(`${config.MY_TAG} ACK received for node ${key}`);
            }
        }
    } catch (_) {}
});

function publishCommand(node_id, star_id, payload, attempt) {
    const topic = `greenhouse/commands/${star_id}`;
    mqttClient.publish(topic, payload, { qos: 1 });
    console.log(`${config.MY_TAG} Command -> ${topic} (attempt ${attempt}/${config.COMMAND_MAX_ATTEMPTS}): ${payload}`);
}

function scheduleRetry(node_id) {
    const key = String(node_id);
    const pending = pendingCommands[key];
    if (!pending) return;
    
    pending.timer = setTimeout(() => {
        if (!pendingCommands[key]) return;
        if (pending.attempts >= config.COMMAND_MAX_ATTEMPTS) {
            console.error(`${config.MY_TAG} Command to node ${node_id} unacknowledged after ${config.COMMAND_MAX_ATTEMPTS} attempts`);
            delete pendingCommands[key];
            return;
        }
        pending.attempts++;
        publishCommand(node_id, pending.star_id, pending.payload, pending.attempts);
        scheduleRetry(node_id);
    }, config.COMMAND_TIMEOUT_MS);
}

function sendCommand(node_id, actuator, value, duration_s) {
    const star_id = topologyService.getStarId(node_id);
    if (!star_id) {
        throw new Error(`No star known for node_id ${node_id}. Wait for the node to send at least one telemetry packet.`);
    }

    let dur = Number(duration_s) || 0;
    if (dur < 0) dur = 0;
    if (dur > config.MAX_COMMAND_DURATION_S) {
        console.log(`${config.MY_TAG} Duration ${dur}s exceeds max, clamping to ${config.MAX_COMMAND_DURATION_S}s`);
        dur = config.MAX_COMMAND_DURATION_S;
    }

    let val = Number(value) || 0;
    if (val < 0) val = 0;
    if (val > 255) {
        console.log(`${config.MY_TAG} Value ${val} exceeds max, clamping to 255`);
        val = 255;
    }

    const payload = JSON.stringify({
        nid: Number(node_id),
        act: String(actuator),
        val,
        dur
    });

    const key = String(node_id);
    if (pendingCommands[key]) clearTimeout(pendingCommands[key].timer);
    pendingCommands[key] = { star_id, payload, attempts: 1, timer: null };

    publishCommand(node_id, star_id, payload, 1);
    scheduleRetry(node_id);

    return { status: 'sent', star_id, node_id, topic: `greenhouse/commands/${star_id}` };
}

module.exports = { sendCommand };