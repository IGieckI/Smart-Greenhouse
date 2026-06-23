const express = require('express');
const fs = require('fs');
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

// Variabili globali per tracciare l'utilizzo degli ID
let fallbackUsageCount = {};
let currentFallbackId = null;


const sampling_freq_min = 6
const assumption_equals = 2
const tollerance = 2 
const FALLBACK_THR = (sampling_freq_min * assumption_equals + tollerance)


const { exec } = require('child_process');
const path = require('path');

// Assicurati che il file audio sia nella cartella montata dal volume
const AUDIO_FILE = path.join(__dirname, 'ringtone.mp3'); 


// --- NUOVA FUNZIONE PER I SUONI ---
// Emette N bip distanziati di 400ms l'uno dall'altro
const playBeeps = async (times) => {
    // for (let i = 0; i < times; i++) {
    //     process.stdout.write('\x07'); 
    //     await new Promise(resolve => setTimeout(resolve, 400)); 
    // }

    for (let i = 0; i < times; i++) {
    // for (let j = 0; j < (i * 3) + 1; j ++){
        // console.log(`Riproduzione suono ${i + 1} di ${times}...`);
    
        await new Promise((resolve, reject) => {
            // Se usi un .mp3 usa 'mpg123'. Se usi un .wav usa 'aplay'
            exec(`mpg123 ${AUDIO_FILE}`, (error, stdout, stderr) => {
                if (error) {
                    console.error(`Errore durante la riproduzione: ${error.message}`);
                    resolve(); // Risolviamo comunque per non bloccare il loop
                    return;
                }
                resolve();
            });
        });
        
        // Pausa opzionale tra una riproduzione e l'altra (es. 400ms)
        await new Promise(resolve => setTimeout(resolve, 400)); 
    // }
    }
};



app.post('/api/data', async (req, res) => {
    let data = req.body;

    console.log(data)
    
    if (!data.node_id) {
        return res.status(400).send({ error: "node_id mancante" });
    }

    // Gestione File TXT per Temperatura Fogliare
    if ((data[LEAF_TEMP_LABEL] === undefined) || (data[LEAF_TEMP_LABEL] < 5.0)) {
        try {
            if (fs.existsSync(FALLBACK_FILE_PATH)) {
                // Legge il file e rimuove spazi vuoti o a capo
                const fileContent = fs.readFileSync(FALLBACK_FILE_PATH, 'utf8').trim(); 
                const parts = fileContent.split('/');
                
                if (parts.length === 2) {
                    // Sostituisce l'eventuale virgola italiana con il punto decimale
                    let tempVal = parseFloat(parts[0].replace(',', '.'));
                    let tempId = parts[1];


                    data[LEAF_TEMP_LABEL] = tempVal;

                    // // Se l'ID nel file cambia, resettiamo il focus sul nuovo ID
                    // if (currentFallbackId !== tempId) {
                    //     currentFallbackId = tempId;
                    //     if (fallbackUsageCount[tempId] === undefined) {
                    //         fallbackUsageCount[tempId] = 0;
                    //     }
                    // }
                    if (currentFallbackId !== tempId) {
                        currentFallbackId = tempId;
                        // Azzera SEMPRE il contatore quando viene rilevato un nuovo cambio nel file
                        fallbackUsageCount[tempId] = 0; 
                    }

                    // Controllo utilizzi
                    if (fallbackUsageCount[tempId] < FALLBACK_THR) {
                        data[LEAF_TEMP_LABEL] = tempVal;

                        let currentCount = fallbackUsageCount[tempId]; // Salvi il valore ATTUALE (es. 0, 6, 12)
                        fallbackUsageCount[tempId]++; // Incrementi per il prossimo giro
                        
                        console.log(`[Controller] Temp fogliare letta: ${tempVal}°C (ID: ${tempId}, Utilizzo: ${currentCount + 1}/${FALLBACK_THR})`);
                        
                        if (currentCount % sampling_freq_min === 0){
                            let numBeeps = (currentCount / sampling_freq_min) + 1;
                            // SUONO: Suona tante volte quanto è il counter (1 bip, 2 bip, o ${FALLBACK_THR} bip)
                            playBeeps(numBeeps);
                        }
                        

                    } else {
                        console.log(`[Controller] ATTENZIONE: L'ID '${tempId}' è stato usato ${FALLBACK_THR} volte. Dato fogliare SCARTATO. Aggiorna il file txt!`);
                        
                        // SUONO DI EMERGENZA MASSIMA: 5 bip per indicare che il dato viene ormai scartato
                        playBeeps(5);
                    }
                } else {
                    console.error("[Controller] Formato txt errato. Usa il formato: 21.5/A");
                }
            }
        } catch (error) {
            console.error(`[Controller] Errore lettura file fallback: ${error.message}`);
        }
    }

    // Salvataggio dinamico su InfluxDB
    try {
        const point = new Point('sensor_measurements')
            .tag('id_board', String(data.node_id));
            
        // Aggiunge dinamicamente tutti i valori numerici presenti nel payload
        // const numericFields = ['air_temp', 'air_hum', 'air_press', 'soil_temp', 'soil_hum', 'tds', 'irradiation', ];
        const numericFields = ["air_temp", "humidity", "pressure", 'water_temp', "soil_moisture", "tds", "light_lux", LEAF_TEMP_LABEL]

        for (const field of numericFields) {
            if (data[field] !== undefined && !isNaN(data[field])) {
                point.floatField(field, Number(data[field]));
            }
        }

        console.log(point)

        writeApi.writePoint(point);
        await writeApi.flush(); // <-- Aggiungi await

        console.log(`[Controller] Dati scritti su Influx per la board: ${data.node_id}`);
        res.status(200).send({ status: "success" });
    } catch (error) {
        console.error("[Controller] Errore scrittura Influx:", error);
        res.status(500).send({ error: "Errore database" });
    }
});

const PORT = process.env.PORT || 3001;
app.listen(PORT, () => {
    console.log(`Controller in ascolto sulla porta ${PORT}`);
});