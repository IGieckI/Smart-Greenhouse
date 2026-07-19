const express = require('express');
const config = require('./config');
const topologyService = require('./topology');
const mqttService = require('./mqtt');
const influxService = require('./influx');
const fallbackService = require('./fallback');
const ruleEngine = require('./rules');

const app = express();
app.use(express.json());

topologyService.loadTopology();

app.post('/api/data', async (req, res) => {
    let data = req.body;
    const timestamp = new Date().toISOString();

    console.log(`[${timestamp}] Incoming data:`, data);

    if (!data.node_id) {
        return res.status(400).send({ error: "Missing node_id" });
    }

    console.log("\n\n\n")
    console.log(data)
    console.log(data.star_id)
    console.log("\n\n\n")

    if (data.star_id) {
        topologyService.updateTopology(data.node_id, data.star_id);
    }

    data = fallbackService.processLeafTemp(data);

    ruleEngine.applyRules(data.node_id, data);

    try {
        await influxService.writeTelemetry(data.node_id, data);
        console.log(`${config.MY_TAG} - ${timestamp} - Data successfully written to Influx for board: ${data.node_id}`);
        res.status(200).send({ status: "success" });
    } catch (error) {
        console.error(`${config.MY_TAG} - ${timestamp} - Influx write error:`, error);
        res.status(500).send({ error: "Database error" });
    }
});

app.post('/api/command', (req, res) => {
    const { node_id, actuator, value, duration_s } = req.body;

    if (!node_id || !actuator || value === undefined) {
        return res.status(400).send({ error: 'node_id, actuator, value required' });
    }

    try {
        const result = mqttService.sendCommand(node_id, actuator, value, duration_s);
        res.status(200).send(result);
    } catch (error) {
        if (error.message.includes('No star known')) {
            return res.status(404).send({ error: error.message });
        }
        res.status(500).send({ error: error.message });
    }
});

app.get('/api/topology', (req, res) => {
    res.status(200).json(topologyService.getTopology());
});

app.listen(config.PORT, () => {
    console.log(`Controller listening on port ${config.PORT}`);
});