import os
import sys
import json
import joblib
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel
from typing import Optional, List

from influxdb_client import InfluxDBClient

# Import condiviso
sys.path.append('/app')
from shared_core.preprocessing import create_lagged_features

app = FastAPI(title="Multi-Task IoT Inference API (Recursive Forecasting)")

# Configurazione
INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
INFLUX_ORG = os.getenv("INFLUX_ORG", "iot_org")
BUCKET_CLEAN = "sensor_data_clean"

BASE_MODEL_DIR = "/app/shared_core/models"

# Definizione Task con indicazione degli step di bacheca/orizzonte temporale
TASKS = {
    "v1": {
        "target": "leaf_temp",
        "features": ['air_temp', 'humidity', 'pressure', 'water_temp', 'tds', 'soil_moisture', 'light_lux'],
        "steps": 1
    },
    "v2": {
        "target": "leaf_temp", 
        "features": ['air_temp', 'humidity', 'pressure', 'water_temp', 'tds', 'soil_moisture', 'light_lux'],
        "steps": 6
    }
}

# Dizionari globali per mantenere i modelli in memoria
loaded_models = {}
loaded_scalers = {}
loaded_info = {}

@app.on_event("startup")
def load_assets():
    """Carica dinamicamente i modelli per ogni task e valida le configurazioni."""
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
    """Recupera le ultime 'limit' righe di dati da InfluxDB per la board specificata."""
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


def prepare_and_predict_recursive(task: str, df_history: pd.DataFrame) -> tuple[List[float], str]:
    """
    Esegue una predizione autoregressiva ricorsiva multi-step.
    Popola i lag dinamicamente usando le predizioni generate nei passi precedenti.
    """
    target = TASKS[task]["target"]
    features = TASKS[task]["features"]
    steps = TASKS[task]["steps"]
    
    scaler = loaded_scalers[task]
    model = loaded_models[task]
    model_name = loaded_info.get(task, {}).get("best_model", "Unknown")
    
    lagged_columns = [col for col in scaler.feature_names_in_]
    
    # Copia di lavoro per non sporcare i dati iniziali
    df_working = df_history.copy()
    predictions = []
    
    for i in range(steps):
        # 1. Rigenera i lag includendo le predizioni dei cicli precedenti
        df_lagged = create_lagged_features(df_working, target, features, lags=6)
        df_lagged.dropna(inplace=True)
        
        if df_lagged.empty:
            raise HTTPException(
                status_code=400, 
                detail=f"Impossibile generare i lag allo step {i+1}. Verificare la consistenza dei dati."
            )
            
        # 2. Isola l'orizzonte temporale corrente ed effettua lo scaling
        X_current = df_lagged[lagged_columns].iloc[-1:]
        X_scaled = scaler.transform(X_current)
        
        # 3. Inferenza (Predizione del singolo step)
        if "ARIMA" in model_name.upper():
            pred_raw = model.predict(n_periods=1, X=X_scaled)
            pred_val = float(pred_raw.iloc[0] if hasattr(pred_raw, "iloc") else pred_raw[0])
        else:
            pred_raw = model.predict(X_scaled)
            pred_val = float(pred_raw[0])
            
        predictions.append(round(pred_val, 3))
        
        # 4. Se mancano altri step, prepara il dataframe per il ciclo autoregressivo successivo
        if i < steps - 1:
            # Sovrascrive il target corrente con il valore predetto
            target_idx = df_working.columns.get_loc(target)
            df_working.iloc[-1, target_idx] = pred_val
            
            # Crea una nuova riga simulata (frequenza campionamento nominale: 5 minuti)
            next_idx = df_working.index[-1] + pd.Timedelta(minutes=5)
            next_row = df_working.iloc[-1:].copy()
            next_row.index = [next_idx]
            
            # Resetta il target per il nuovo step futuro (le features esogene vengono traslate costanti)
            next_row[target] = np.nan
            df_working = pd.concat([df_working, next_row])
            
    return predictions, model_name


@app.get("/predict/{task}/latest")
def predict_latest(task: str, board_id: str = "9"):
    """
    MODALITÀ AUTOMATICA: Recupera la history da InfluxDB, genera i lag iniziali 
    e calcola una sequenza ricorsiva di predizioni (1 per v1, 6 per v2).
    """
    if task not in TASKS or task not in loaded_models:
        raise HTTPException(status_code=404, detail=f"Task {task} non configurato o modello assente.")
        
    df_history = fetch_historical_data(board_id, limit=7)
    if len(df_history) < 7:
        raise HTTPException(status_code=400, detail=f"Trovati solo {len(df_history)} record per la board {board_id}. Ne servono almeno 7 per inizializzare i lags.")
        
    pred_list, model_name = prepare_and_predict_recursive(task, df_history)
    
    return {
        "task": task,
        "mode": "automatic_from_influx",
        "target": TASKS[task]["target"],
        "model_used": model_name,
        "prediction_steps": len(pred_list),
        "predictions": pred_list
    }


@app.post("/predict/{task}/manual")
def predict_manual(task: str, data: SensorData, board_id: str = "9"):
    """
    MODALITÀ MANUALE: L'utente passa le letture attuali nel body. L'API recupera i 6 record 
    passati da InfluxDB, appende l'input dell'utente ed esegue la proiezione nel futuro.
    """
    if task not in TASKS or task not in loaded_models:
        raise HTTPException(status_code=404, detail=f"Task {task} non configurato o modello assente.")
        
    df_history = fetch_historical_data(board_id, limit=6)
    if len(df_history) < 6:
        raise HTTPException(status_code=400, detail="Storico su Influx insufficiente per agganciare i dati manuali e generare i lag.")
        
    # Costruzione del record manuale utente
    new_idx = pd.Timestamp.utcnow()
    new_data_dict = data.dict(exclude_unset=True)
    df_new = pd.DataFrame([new_data_dict], index=[new_idx])
    
    # Allineamento colonne e concatenazione
    df_combined = pd.concat([df_history, df_new])
    
    pred_list, model_name = prepare_and_predict_recursive(task, df_combined)
    
    return {
        "task": task,
        "mode": "manual_override",
        "target": TASKS[task]["target"],
        "model_used": model_name,
        "prediction_steps": len(pred_list),
        "predictions": pred_list
    }


@app.get("/info/{task}")
def get_task_info(task: str):
    """Endpoint per leggere le metriche e le informazioni del miglior modello di un dato task."""
    if task not in loaded_info:
        raise HTTPException(status_code=404, detail=f"Metriche non trovate per {task}.")
    return loaded_info[task]