const fs = require('fs');
const { exec } = require('child_process');
const config = require('./config');

let fallbackUsageCount = {};
let currentFallbackIds = {};

const delay = (ms) => new Promise(resolve => setTimeout(resolve, ms));

const playBeeps = async (times) => {
    for (let i = 0; i < times; i++) {
        await new Promise((resolve) => {
            exec(`mpg123 ${config.AUDIO_FILE}`, (error) => {
                if (error) {
                    console.error(`Playback error: ${error.message}`);
                }
                resolve();
            });
        });
        await delay(400);
    }
};

function processLeafTemp(data) {
    if ((data[config.LEAF_TEMP_LABEL] === undefined) || (data[config.LEAF_TEMP_LABEL] < 5.0)) {
        try {
            if (fs.existsSync(config.FALLBACK_FILE_PATH)) {
                const fileContent = fs.readFileSync(config.FALLBACK_FILE_PATH, 'utf8').trim();
                const lines = fileContent.split(/\r?\n/).filter(line => line.trim() !== '');
                
                let boardFallback = null;
                
                for (const line of lines) {
                    const parts = line.split('/');
                    if (parts.length === 3 && parts[0] === String(data.node_id)) {
                        boardFallback = parts;
                        break;
                    }
                }
                
                if (boardFallback) {
                    const nodeIdStr = String(data.node_id);
                    const tempVal = parseFloat(boardFallback[1].replace(',', '.'));
                    const tempId = boardFallback[2].trim();
                    const usageKey = `${nodeIdStr}_${tempId}`;
                    
                    if (currentFallbackIds[nodeIdStr] !== tempId) {
                        currentFallbackIds[nodeIdStr] = tempId;
                        fallbackUsageCount[usageKey] = 0;
                    }

                    if (fallbackUsageCount[usageKey] < config.FALLBACK_THR) {
                        data[config.LEAF_TEMP_LABEL] = tempVal;
                        const currentCount = fallbackUsageCount[usageKey];
                        fallbackUsageCount[usageKey]++;
                        
                        console.log(`${config.MY_TAG} Leaf temp read: ${tempVal}°C for board ${nodeIdStr} (ID: ${tempId}, Usage: ${currentCount + 1}/${config.FALLBACK_THR})`);
                        
                        if (currentCount % config.SAMPLING_FREQ_MIN === 0) {
                            const numBeeps = (currentCount / config.SAMPLING_FREQ_MIN) + 1;
                            playBeeps(numBeeps);
                        }
                    } else {
                        console.log(`${config.MY_TAG} WARNING: ID '${tempId}' for board ${nodeIdStr} has been used ${config.FALLBACK_THR} times. Leaf data DISCARDED. Update the txt file!`);
                        playBeeps(5);
                    }
                } else {
                    console.error(`${config.MY_TAG} leaf_temp undefined -> No fallback entry found for board ${data.node_id} (Use layout: 3750846324/21.5/A)`);
                    data[config.LEAF_TEMP_LABEL] = undefined;
                }
            }
        } catch (error) {
            console.error(`${config.MY_TAG} Fallback file read error: ${error.message}`);
        }
    }
    return data;
}

module.exports = { processLeafTemp };