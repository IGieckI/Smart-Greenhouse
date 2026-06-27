import os
import sys
import json
import joblib
import copy
import time
import threading # <-- IMPORTANTE
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional

from shared_core.data_sync import sync_clean_bucket
from shared_core.tasks import TASKS
from shared_core.config import *

sys.path.append('/app')
from shared_core.predictor import recursive_multistep_inference

app = FastAPI(title="Multi-Task IoT Inference API (Recursive Forecasting)")

# Variabili Globali per i Modelli in RAM
loaded_models = {}
loaded_info = {}
loaded_env_arimas = {}


# ==========================================
# GESTIONE CONCORRENZA JIT SYNC
# ==========================================
sync_lock = threading.Lock()
LAST_SYNC_TIME = 0.0
SYNC_COOLDOWN_SECONDS = 30.0  # Evita sync multipli se richieste vicinissime

@app.on_event("startup")
def load_assets():
    """Carica dinamicamente i modelli ML (Pipeline) e gli ARIMA Ambientali."""
    # 1. Carica i Modelli ML (Task)
    for task in TASKS.keys():
        best_dir = os.path.join(BASE_MODEL_DIR, task, "best_model")
        model_path = os.path.join(best_dir, "best_model.joblib")
        info_path = os.path.join(best_dir, "best_model_info.json")

        if os.path.exists(model_path):
            loaded_models[task] = joblib.load(model_path)
            if os.path.exists(info_path):
                with open(info_path, "r") as f:
                    loaded_info[task] = json.load(f)
            print(f"[{task.upper()}] Pipeline ML caricata con successo in RAM!")
        else:
            print(f"[{task.upper()}] ATTENZIONE: Artefatti non trovati. Esegui prima il training.")
            
    # 2. Carica i Modelli ARIMA Ambientali pre-addestrati
    env_dir = os.path.join(BASE_MODEL_DIR, "env_forecasters")
    if os.path.exists(env_dir):
        for feat in TASKS["t1"]["features"]:
            arima_path = os.path.join(env_dir, f"arima_{feat}.joblib")
            if os.path.exists(arima_path):
                loaded_env_arimas[feat] = joblib.load(arima_path)
        print("[ENV] Modelli ARIMA ambientali caricati in RAM!")


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
    """Sincronizza il DB in modo Thread-Safe, poi recupera dal DB pulito."""
    from influxdb_client import InfluxDBClient
    global LAST_SYNC_TIME
    
    # 1. BLOCCO MUTEX CON COOLDOWN
    with sync_lock:
        current_time = time.time()
        if current_time - LAST_SYNC_TIME > SYNC_COOLDOWN_SECONDS:
            try:
                sync_clean_bucket(INFLUX_URL, INFLUX_TOKEN, INFLUX_ORG)
                LAST_SYNC_TIME = time.time()
            except Exception as e:
                print(f"[API] Errore durante la sincronizzazione JIT: {e}")

    # 2. QUERY AL DATABASE PULITO
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    query = f'''
        from(bucket: "{BUCKET_CLEAN}")
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


@app.get("/predict/{task}/latest")
def predict_latest(task: str, board_id: str = "9"):
    if task not in TASKS or task not in loaded_models:
        raise HTTPException(status_code=404, detail=f"Task {task} non configurato o modello assente.")
        
    df_history = fetch_historical_data(board_id, limit=FETCH_LIMIT_LATEST)
    if len(df_history) < DEFAULT_LAGS + 1:
        raise HTTPException(status_code=400, detail=f"Dati insufficienti su InfluxDB per la board {board_id}.")

    # ==========================================
    # CIRCUIT BREAKER: CONTROLLO DEI BUCHI NERI
    # ==========================================
    time_span = df_history.index[-1] - df_history.index[0]
    expected_span = pd.Timedelta(minutes=NOMINAL_FREQ_MINUTES * (len(df_history) - 1))
    
    if abs(time_span - expected_span) > pd.Timedelta(minutes=20):
        raise HTTPException(
            status_code=400, 
            detail=f"Integrità temporale compromessa. Rilevato un buco nei dati nei recenti log della board {board_id}. Impossibile garantire un forecasting affidabile."
        )

    # Preparazione ARIMA locali
    local_arimas = {}
    for feat, arima_model in loaded_env_arimas.items():
        if feat in df_history.columns:
            obs = df_history[feat].dropna().values
            local_model = copy.deepcopy(arima_model)
            local_model.update(obs)  
            local_arimas[feat] = local_model

    pred_list = recursive_multistep_inference(
        T_current_data=df_history,
        arima_models=local_arimas,
        ml_model_pipeline=loaded_models[task],
        task_config=TASKS[task],
        steps=TASKS[task]["steps"] 
    )
    
    return {
        "task": task,
        "mode": "automatic_from_influx",
        "target": TASKS[task]["target"],
        "model_used": loaded_info.get(task, {}).get("best_model", "Unknown"),
        "prediction_steps": len(pred_list),
        "predictions": pred_list
    }


@app.post("/predict/{task}/manual")
def predict_manual(task: str, data: SensorData, board_id: str = "9"):
    if task not in TASKS or task not in loaded_models:
        raise HTTPException(status_code=404, detail=f"Task {task} non configurato o modello assente.")
        
    df_history = fetch_historical_data(board_id, limit=FETCH_LIMIT_MANUAL)
    if len(df_history) < DEFAULT_LAGS:
        raise HTTPException(status_code=400, detail="Storico Influx insufficiente per generare i lag.")
        
    new_idx = pd.Timestamp.utcnow()
    new_data_dict = data.dict(exclude_unset=True)
    df_new = pd.DataFrame([new_data_dict], index=[new_idx])
    df_combined = pd.concat([df_history, df_new])
    
    # Preparazione ARIMA locali 
    local_arimas = {}
    for feat, arima_model in loaded_env_arimas.items():
        if feat in df_combined.columns:
            obs = df_combined[feat].dropna().values
            local_model = copy.deepcopy(arima_model)
            local_model.update(obs)
            local_arimas[feat] = local_model

    # Chiamata pulita al motore in predictor.py
    pred_list = recursive_multistep_inference(
        T_current_data=df_combined,
        arima_models=local_arimas,
        ml_model_pipeline=loaded_models[task],
        task_config=TASKS[task],
        steps=TASKS[task]["steps"]
    )
    
    return {
        "task": task,
        "mode": "manual_override",
        "target": TASKS[task]["target"],
        "model_used": loaded_info.get(task, {}).get("best_model", "Unknown"),
        "prediction_steps": len(pred_list),
        "predictions": pred_list
    }


@app.get("/info/{task}")
def get_task_info(task: str):
    if task not in loaded_info:
        raise HTTPException(status_code=404, detail=f"Metriche non trovate per {task}.")
    return loaded_info[task]