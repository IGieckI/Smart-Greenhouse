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
from statsmodels.tsa.arima.model import ARIMA

from shared_core.data_sync import sync_clean_bucket
from shared_core.tasks import TASKS

# Import condiviso
sys.path.append('/app')
from shared_core.preprocessing import (
    create_lagged_features, 
    identify_leaf_steps, 
    gaussian_weighted_interpolation, 
    clean_anomalies
)

app = FastAPI(title="Multi-Task IoT Inference API (Recursive Forecasting)")


app = FastAPI(title="Multi-Task IoT Inference API (Recursive Forecasting)")

# Configurazione
INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
INFLUX_ORG = os.getenv("INFLUX_ORG", "iot_org")
BUCKET_CLEAN = "sensor_data_clean"
BASE_MODEL_DIR = "/app/shared_core/models"

# Dizionari globali per mantenere i modelli in memoria
loaded_models = {}
loaded_scalers = {}
loaded_info = {}
env_arima_orders = {}



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
    
    # Caricamento ordini ARIMA Ambientali
    env_path = os.path.join(BASE_MODEL_DIR, "env_forecasters", "env_arima_orders.json")
    if os.path.exists(env_path):
        global env_arima_orders
        with open(env_path, "r") as f:
            env_arima_orders = json.load(f)
        print("[ENV] Configurazioni ARIMA ambientali caricate!")


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
    """Sincronizza il DB, poi recupera dal DB pulito."""
    try:
        sync_clean_bucket(INFLUX_URL, INFLUX_TOKEN, INFLUX_ORG)
    except Exception as e:
        print(f"[API] Errore durante la sincronizzazione JIT: {e}")

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

def prepare_and_predict(task: str, df_history: pd.DataFrame) -> tuple[List[float], str]:
    config = TASKS[task]
    target = config["target"]
    features = config["features"]
    steps = config["steps"]
    use_lags = config.get("use_lags", False)
    lag_target = config.get("lag_target", True)
    
    scaler = loaded_scalers[task]
    model = loaded_models[task]
    model_name = loaded_info.get(task, {}).get("best_model", "Unknown")
    model_features = [col for col in scaler.feature_names_in_]
    predictions = []
    
    # === CASO T1: Puntuale ===
    if not use_lags:
        X_current = df_history[features].iloc[-1:]
        X_scaled = scaler.transform(X_current)
        
        pred_raw = model.predict(X_scaled)
        pred_val = float(pred_raw[0])
        predictions.append(round(pred_val, 3))
        
        return predictions, model_name

    # === CASO T2 & T3: Orizzonte Temporale ===
    env_forecasts = {}
    for feat in features:
        order = env_arima_orders.get(feat, (1, 1, 1))
        series = df_history[feat].ffill().bfill().values
        arima_model = ARIMA(series, order=order).fit()
        # FIX: Converto in lista per evitare problemi di indicizzazione (KeyError) con Pandas
        env_forecasts[feat] = arima_model.forecast(steps=steps).tolist()

    df_working = df_history.copy()

    for i in range(steps):
        df_lagged = create_lagged_features(df_working, target, features, lags=6, lag_target=lag_target)
        df_lagged.dropna(inplace=True)
        
        if df_lagged.empty:
            raise HTTPException(status_code=400, detail=f"Errore generazione lag allo step {i+1}.")
            
        X_current = df_lagged[model_features].iloc[-1:]
        X_scaled = scaler.transform(X_current)
        
        if "ARIMA" in model_name.upper():
            pred_raw = model.predict(n_periods=1, X=X_scaled)
            pred_val = float(pred_raw.iloc[0] if hasattr(pred_raw, "iloc") else pred_raw[0])
        else:
            pred_val = float(model.predict(X_scaled)[0])
            
        predictions.append(round(pred_val, 3))
        
        if i < steps - 1:
            target_idx = df_working.columns.get_loc(target)
            df_working.iloc[-1, target_idx] = pred_val
            
            next_idx = df_working.index[-1] + pd.Timedelta(minutes=6)
            next_row = df_working.iloc[-1:].copy()
            next_row.index = [next_idx]
            
            for feat in features:
                next_row[feat] = env_forecasts[feat][i]
                
            next_row[target] = np.nan
            df_working = pd.concat([df_working, next_row])
            
    return predictions, model_name
    
    # for i in range(steps):
    #     df_lagged = create_lagged_features(df_working, target, features, lags=6)
    #     df_lagged.dropna(inplace=True)
        
    #     if df_lagged.empty:
    #         raise HTTPException(status_code=400, detail=f"Impossibile generare i lag allo step {i+1}.")
            
    #     X_current = df_lagged[model_features].iloc[-1:]
    #     X_scaled = scaler.transform(X_current)
        
    #     if "ARIMA" in model_name.upper():
    #         pred_raw = model.predict(n_periods=1, X=X_scaled)
    #         pred_val = float(pred_raw.iloc[0] if hasattr(pred_raw, "iloc") else pred_raw[0])
    #     else:
    #         pred_raw = model.predict(X_scaled)
    #         pred_val = float(pred_raw[0])
            
    #     predictions.append(round(pred_val, 3))
        
    #     if i < steps - 1:
    #         target_idx = df_working.columns.get_loc(target)
    #         df_working.iloc[-1, target_idx] = pred_val
            
    #         next_idx = df_working.index[-1] + pd.Timedelta(minutes=5)
    #         next_row = df_working.iloc[-1:].copy()
    #         next_row.index = [next_idx]
    #         next_row[target] = np.nan
    #         df_working = pd.concat([df_working, next_row])
            
    # return predictions, model_name

@app.get("/predict/{task}/latest")
def predict_latest(task: str, board_id: str = "9"):
    if task not in TASKS or task not in loaded_models:
        raise HTTPException(status_code=404, detail=f"Task {task} non configurato o modello assente.")
        
    df_history = fetch_historical_data(board_id, limit=60)
    if len(df_history) < 7:
        raise HTTPException(status_code=400, detail=f"Trovati solo {len(df_history)} record per la board {board_id}. Ne servono almeno 7.")
        
    pred_list, model_name = prepare_and_predict(task, df_history)
    
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
    if task not in TASKS or task not in loaded_models:
        raise HTTPException(status_code=404, detail=f"Task {task} non configurato o modello assente.")
        
    df_history = fetch_historical_data(board_id, limit=59)
    if len(df_history) < 6:
        raise HTTPException(status_code=400, detail="Storico su Influx insufficiente per agganciare i dati manuali e generare i lag.")
        
    new_idx = pd.Timestamp.utcnow()
    new_data_dict = data.dict(exclude_unset=True)
    df_new = pd.DataFrame([new_data_dict], index=[new_idx])
    
    df_combined = pd.concat([df_history, df_new])
    pred_list, model_name = prepare_and_predict(task, df_combined)
    
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
    if task not in loaded_info:
        raise HTTPException(status_code=404, detail=f"Metriche non trovate per {task}.")
    return loaded_info[task]