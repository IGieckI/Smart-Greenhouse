import os
import sys
import json
import joblib
import pandas as pd
from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel
from typing import Optional

from influxdb_client import InfluxDBClient

# Import condiviso
sys.path.append('/app')
from shared_core.preprocessing import create_lagged_features

app = FastAPI(title="Multi-Task IoT Inference API")

# Configurazione
INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
INFLUX_ORG = os.getenv("INFLUX_ORG", "iot_org")
BUCKET_CLEAN = "sensor_data_clean"

BASE_MODEL_DIR = "/app/shared_core/models"

# Definizione Task (Stessa usata nel trainer)
TASKS = {
    "v1": {
        "target": "leaf_temp",
        "features": ['air_temp', 'humidity', 'pressure', 'water_temp', 'tds', 'soil_moisture', 'light_lux']
    },
    "v2": {
        "target": "leaf_temp", 
        "features": ['air_temp', 'humidity', 'pressure', 'water_temp', 'tds', 'soil_moisture', 'light_lux']
    }
}

# Dizionari globali per mantenere i modelli in memoria
loaded_models = {}
loaded_scalers = {}
loaded_info = {}

@app.on_event("startup")
def load_assets():
    """Carica dinamicamente i modelli per ogni task."""
    for task in TASKS.keys():
        best_dir = os.path.join(BASE_MODEL_DIR, task, "best_model")
        model_path = os.path.join(best_dir, "best_model.joblib")
        scaler_path = os.path.join(best_dir, "scaler.joblib")
        info_path = os.path.join(best_dir, "best_model_info.json")

        if os.path.exists(model_path) and os.path.exists(scaler_path):
            loaded_models[task] = joblib.load(model_path)
            loaded_scalers[task] = joblib.load(scaler_path)
            if os.path.exists(info_path):
                with open(info_path, "r") as f:
                    loaded_info[task] = json.load(f)
            print(f"[{task.upper()}] Modello e Scaler caricati con successo in RAM!")
        else:
            print(f"[{task.upper()}] ATTENZIONE: Artefatti non trovati. Esegui prima il training.")


# Modello Pydantic flessibile (include tutte le possibili features)
class SensorData(BaseModel):
    air_temp: Optional[float] = None
    humidity: Optional[float] = None
    pressure: Optional[float] = None
    water_temp: Optional[float] = None
    tds: Optional[float] = None
    soil_moisture: Optional[float] = None
    light_lux: Optional[float] = None
    leaf_temp: Optional[float] = None


def fetch_historical_data(board_id: str, limit: int) -> pd.DataFrame:
    """Recupera le ultime 'limit' righe di dati dalla dashboard InfluxDB per la board specificata."""
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    query = f'''
        from(bucket: "{BUCKET_CLEAN}")
          |> range(start: -7d)
          |> filter(fn: (r) => r._measurement == "sensor_measurements")
          |> filter(fn: (r) => r.id_board == "{board_id}")
          |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
          |> tail(n: {limit})
    '''
    try:
        df = client.query_api().query_data_frame(query)
        if isinstance(df, list):
            if len(df) == 0: return pd.DataFrame()
            df = pd.concat(df, ignore_index=True)
        if not df.empty:
            df.set_index('_time', inplace=True)
            df.sort_index(inplace=True)
        return df
    except Exception as e:
        print(f"Errore query Influx: {e}")
        return pd.DataFrame()


def prepare_and_predict(task: str, df_history: pd.DataFrame):
    """Genera i lag, scala l'input e lancia la predizione."""
    target = TASKS[task]["target"]
    features = TASKS[task]["features"]
    
    # Crea le features ritardate (lags=6 come da addestramento)
    df_lagged = create_lagged_features(df_history, target, features, lags=6)
    df_lagged.dropna(inplace=True)
    
    if df_lagged.empty:
        raise HTTPException(status_code=400, detail="Storico insufficiente o dati corrotti post-lagging (richiesti almeno 7 campioni contigui).")
        
    # Estraiamo l'ultima riga generata (che rappresenta lo stato attuale con i 6 lag passati)
    lagged_columns = [col for col in df_lagged.columns if 'lag' in col or col in features]
    X_current = df_lagged[lagged_columns].iloc[-1:]
    
    # Scale & Predict
    scaler = loaded_scalers[task]
    model = loaded_models[task]
    model_name = loaded_info.get(task, {}).get("best_model", "Unknown")
    
    # Riordino esatto colonne per evitare Warning dello scaler
    X_current = X_current[scaler.feature_names_in_]
    X_scaled = scaler.transform(X_current)
    
    # Gestione Caso AutoARIMA vs Modelli Sklearn-like
    if "ARIMA" in model_name.upper():
        prediction = model.predict(n_periods=1, X=X_scaled)
    else:
        prediction = model.predict(X_scaled)
        
    return round(float(prediction[0]), 3), model_name


@app.get("/predict/{task}/latest")
def predict_latest(task: str, board_id: str = "9"):
    """
    MODALITÀ AUTOMATICA: Il sistema recupera gli ultimi 7 dati da InfluxDB, genera
    autonomamente i lagged-features, "finge" di non conoscere la vera y attuale e predice.
    """
    if task not in TASKS or task not in loaded_models:
        raise HTTPException(status_code=404, detail=f"Task {task} non configurato o modello assente.")
        
    df_history = fetch_historical_data(board_id, limit=7)
    if len(df_history) < 7:
        raise HTTPException(status_code=400, detail=f"Trovati solo {len(df_history)} record per la board {board_id}. Ne servono 7 per il lags=6.")
        
    pred_val, model_name = prepare_and_predict(task, df_history)
    
    return {
        "task": task,
        "mode": "automatic_from_influx",
        "target": TASKS[task]["target"],
        "model_used": model_name,
        "prediction": pred_val
    }


@app.post("/predict/{task}/manual")
def predict_manual(task: str, data: SensorData, board_id: str = "9"):
    """
    MODALITÀ MANUALE: L'utente passa le letture attuali nel body. 
    L'API recupera gli ultimi 6 dati storici (necessari per i lag), unisce la 
    nuova lettura utente, genera le features e predice.
    """
    if task not in TASKS or task not in loaded_models:
        raise HTTPException(status_code=404, detail=f"Task {task} non configurato o modello assente.")
        
    # 1. Recupera la storia (ultimi 6)
    df_history = fetch_historical_data(board_id, limit=6)
    if len(df_history) < 6:
        raise HTTPException(status_code=400, detail="Storico su Influx insufficiente per unire i tuoi dati e creare le sequenze autoregressive.")
        
    # 2. Crea il nuovo record coi dati dell'utente, simulando un timestamp futuro
    new_idx = pd.Timestamp.utcnow()
    new_data_dict = data.dict(exclude_unset=True)
    df_new = pd.DataFrame([new_data_dict], index=[new_idx])
    
    # 3. Concatenazione Storia + Presente Forzato
    df_combined = pd.concat([df_history, df_new])
    
    pred_val, model_name = prepare_and_predict(task, df_combined)
    
    return {
        "task": task,
        "mode": "manual_override",
        "target": TASKS[task]["target"],
        "model_used": model_name,
        "prediction": pred_val
    }


@app.get("/info/{task}")
def get_task_info(task: str):
    """Endpoint per leggere le metriche e le informazioni del miglior modello di un dato task."""
    if task not in loaded_info:
        raise HTTPException(status_code=404, detail=f"Metriche non trovate per {task}.")
    return loaded_info[task]