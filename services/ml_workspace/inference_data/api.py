from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import joblib
import os
import pandas as pd

app = FastAPI(title="Leaf Temp Inference API")

# Percorsi dei file condivisi dal volume Docker
SHARED_DIR = "/app/shared_core"
MODEL_V1_PATH = f"{SHARED_DIR}/V1_instant_model.pkl"
SCALER_PATH = f"{SHARED_DIR}/scaler.pkl"
METRICS_V1_PATH = f"{SHARED_DIR}/V1_instant_model_metrics.json"

# Variabili globali per mantenere i modelli in RAM
model_v1 = None
scaler = None

@app.on_event("startup")
def load_assets():
    """Carica i modelli e lo scaler in memoria all'avvio del container."""
    global model_v1, scaler
    if os.path.exists(MODEL_V1_PATH) and os.path.exists(SCALER_PATH):
        model_v1 = joblib.load(MODEL_V1_PATH)
        scaler = joblib.load(SCALER_PATH)
        print("Modelli e Scaler caricati con successo!")
    else:
        print("ATTENZIONE: Modelli non trovati. Il server è su, ma restituirà 503 finché non addestri.")

# Lo schema esatto delle features usate nel train.py
class SensorDataV1(BaseModel):
    air_temp: float
    humidity: float
    pressure: float
    water_temp: float
    tds: float
    soil_moisture: float
    light_lux: float

@app.post("/predict/v1")
def predict_v1(data: SensorDataV1):
    if not model_v1 or not scaler:
        raise HTTPException(status_code=503, detail="Modello o Scaler non presenti. Esegui il training.")
    
    try:
        # 1. Convertiamo in DataFrame per mantenere i nomi delle colonne (lo scaler di sklearn ringrazia)
        input_df = pd.DataFrame([data.model_dump()])
        
        # 2. Normalizziamo l'input
        input_scaled = scaler.transform(input_df)
        
        # 3. Inferenza
        prediction = model_v1.predict(input_scaled)
        
        return {
            "model_used": "V1_instant_model",
            "predicted_leaf_temperature": round(prediction[0], 2)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/info/v1")
def get_v1_info():
    """Endpoint utile per leggere i metadati/iperparametri salvati."""
    if not os.path.exists(METRICS_V1_PATH):
        raise HTTPException(status_code=404, detail="Metriche non trovate.")
    
    import json
    with open(METRICS_V1_PATH, "r") as f:
        return json.load(f)