import os
import sys
import json
import joblib
import copy
import time
import threading
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional

sys.path.append('/app')
from shared_core.data_sync import sync_clean_bucket
from shared_core.tasks import TASKS
from shared_core.config import *
from shared_core.predictor import recursive_multistep_inference

app = FastAPI(title="Multi-Freq IoT Inference API")

# Strutture Dati Dinamiche: dict di dict in base alla frequenza (es. loaded_models['2']['t1'])
loaded_models = {}
loaded_info = {}
loaded_env_arimas = {}

# Gestione JIT separata per frequenza
sync_lock = threading.Lock()
LAST_SYNC_TIME = {} 
SYNC_COOLDOWN_SECONDS = 30.0  

@app.on_event("startup")
def load_assets():
    """Scansiona BASE_MODEL_DIR per caricare i modelli di tutte le frequenze disponibili."""
    if not os.path.exists(BASE_MODEL_DIR): return
    
    for freq_folder in os.listdir(BASE_MODEL_DIR):
        if not freq_folder.endswith('m'): continue
        
        freq_key = freq_folder.replace('m', '') # es. '2', '6'
        freq_path = os.path.join(BASE_MODEL_DIR, freq_folder)
        
        loaded_models[freq_key] = {}
        loaded_info[freq_key] = {}
        loaded_env_arimas[freq_key] = {}
        
        # 1. Carica Pipeline ML
        for task in TASKS.keys():
            best_dir = os.path.join(freq_path, task, "best_model")
            model_path = os.path.join(best_dir, "best_model.joblib")
            info_path = os.path.join(best_dir, "best_model_info.json")
            if os.path.exists(model_path):
                loaded_models[freq_key][task] = joblib.load(model_path)
                if os.path.exists(info_path):
                    with open(info_path, "r") as f:
                        loaded_info[freq_key][task] = json.load(f)
                print(f"[RAM {freq_folder}] Pipeline {task.upper()} caricata!")

        # 2. Carica ARIMA
        env_dir = os.path.join(freq_path, "env_forecasters")
        if os.path.exists(env_dir):
            for feat in TASKS["t1"]["features"]:
                arima_path = os.path.join(env_dir, f"arima_{feat}.joblib")
                if os.path.exists(arima_path):
                    loaded_env_arimas[freq_key][feat] = joblib.load(arima_path)
            print(f"[RAM {freq_folder}] ARIMA caricati!")

class SensorData(BaseModel):
    air_temp: Optional[float] = None
    humidity: Optional[float] = None
    pressure: Optional[float] = None
    water_temp: Optional[float] = None
    tds: Optional[float] = None
    soil_moisture: Optional[float] = None
    light_lux: Optional[float] = None
    leaf_temp: Optional[float] = None

def fetch_historical_data(board_id: str, limit: int, freq_minutes: int) -> pd.DataFrame:
    from influxdb_client import InfluxDBClient
    global LAST_SYNC_TIME
    
    with sync_lock:
        current_time = time.time()
        last_sync = LAST_SYNC_TIME.get(freq_minutes, 0.0)
        if current_time - last_sync > SYNC_COOLDOWN_SECONDS:
            try:
                sync_clean_bucket(INFLUX_URL, INFLUX_TOKEN, INFLUX_ORG, freq_minutes=freq_minutes)
                LAST_SYNC_TIME[freq_minutes] = time.time()
            except Exception as e:
                print(f"[API] Errore JIT Sync ({freq_minutes}m): {e}")

    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    bucket_clean = f"{BUCKET_CLEAN_PREFIX}{freq_minutes}m"
    query = f'''
        from(bucket: "{bucket_clean}")
          |> range(start: {INFERENCE_LOOKBACK_DAYS})
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

@app.get("/predict/{freq_minutes}m/{task}/latest")
def predict_latest(freq_minutes: int, task: str, board_id: str = "9"):
    freq_key = str(freq_minutes)
    if freq_key not in loaded_models or task not in loaded_models[freq_key]:
        raise HTTPException(status_code=404, detail=f"Task {task} o frequenza {freq_minutes}m non configurati.")
        
    fetch_latest, _ = get_fetch_limits(freq_minutes)
    df_history = fetch_historical_data(board_id, limit=fetch_latest, freq_minutes=freq_minutes)
    min_history = get_min_history_records(freq_minutes)
    
    if len(df_history) < min_history:
        raise HTTPException(status_code=400, detail=f"Dati insufficienti per board {board_id}.")

    time_span = df_history.index[-1] - df_history.index[0]
    expected_span = pd.Timedelta(minutes=freq_minutes * (len(df_history) - 1))
    
    # Margine di tolleranza calcolato per assorbire 3-4 sample mancanti
    if abs(time_span - expected_span) > pd.Timedelta(minutes=freq_minutes * 4):
        raise HTTPException(status_code=400, detail=f"Integrità temporale compromessa.")

    local_arimas = {}
    for feat, arima_model in loaded_env_arimas[freq_key].items():
        if feat in df_history.columns:
            obs = df_history[feat].dropna().values
            local_model = copy.deepcopy(arima_model)
            local_model.update(obs)  
            local_arimas[feat] = local_model

    pred_list = recursive_multistep_inference(
        T_current_data=df_history, arima_models=local_arimas,
        ml_model_pipeline=loaded_models[freq_key][task],
        task_config=TASKS[task], freq_minutes=freq_minutes
    )
    
    return {
        "task": task, "frequency": f"{freq_minutes}m", "target": TASKS[task]["target"],
        "model_used": loaded_info[freq_key].get(task, {}).get("best_model", "Unknown"),
        "prediction_steps": len(pred_list), "predictions": pred_list
    }

@app.get("/info/{freq_minutes}m/{task}")
def get_task_info(freq_minutes: int, task: str):
    freq_key = str(freq_minutes)
    if freq_key not in loaded_info or task not in loaded_info[freq_key]:
        raise HTTPException(status_code=404, detail="Metriche non trovate.")
    return loaded_info[freq_key][task]



@app.post("/reload-models")
def reload_models():
    """Forza la rilettura dei modelli dal disco per aggiornare la RAM."""
    try:
        load_assets()
        return {"message": "Modelli ricaricati in RAM con successo.", "status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore nel ricaricare i modelli: {e}")