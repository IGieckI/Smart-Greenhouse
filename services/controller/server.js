const express = require('express');
const fs = require('fs');
const { exec } = require('child_process');
const path = require('path');
const { InfluxDB, Point } = require('@influxdata/influxdb-client');

const app = express();
app.use(express.json());

const url = process.env.INFLUX_URL || 'http://localhost:8086';
const token = process.env.INFLUX_TOKEN;
const org = process.env.INFLUX_ORG;
const bucket = process.env.INFLUX_BUCKET;
const influxDB = new InfluxDB({ url, token });
const writeApi = influxDB.getWriteApi(org, bucket);

const LEAF_TEMP_LABEL = process.env.LEAF_TEMP_LABEL || 'leaf_temperature';
const FALLBACK_FILE_PATH = './data/leaf_temp.txt';
const AUDIO_FILE = path.join(__dirname, 'ringtone.mp3'); 

const SAMPLING_FREQ_MIN = 6;
const ASSUMPTION_EQUALS = 2;
const TOLERANCE = 2; 
const FALLBACK_THR = (SAMPLING_FREQ_MIN * ASSUMPTION_EQUALS + TOLERANCE);

const NUMERIC_FIELDS = ["air_temp", "humidity", "pressure", "water_temp", "soil_moisture", "tds", "light_lux", LEAF_TEMP_LABEL];

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
    console.log(data);
    
    if (!data.node_id) {
        return res.status(400).send({ error: "Missing node_id" });
    }

    // Leaf Temperature Fallback Mechanism
    if (data[LEAF_TEMP_LABEL] === undefined || data[LEAF_TEMP_LABEL] < 5.0) {
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

const PORT = process.env.PORT || 3001;
app.listen(PORT, () => {
    console.log(`Controller listening on port ${PORT}`);
});