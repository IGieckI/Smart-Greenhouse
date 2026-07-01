import os
import sys
import json
import joblib
import copy
import time
import threading
import numpy as np
import pandas as pd

# --- FIX: INJECT CMDSTAN ENVIRONMENT VARIABLE BEFORE IMPORTING PROPHET ---
import cmdstanpy
try:
    os.environ['CMDSTAN'] = cmdstanpy.cmdstan_path()
except Exception as e:
    print(f"[Warning] Unable to dynamically set CMDSTAN: {e}")
# -------------------------------------------------------------------------

from prophet.serialize import model_from_json

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

sys.path.append('/app')
from shared_core.data_sync import sync_clean_bucket
from shared_core.tasks import TASKS
from shared_core.config import *
from shared_core.preprocessing import build_advanced_features
from .predictor import recursive_multistep_inference, ensemble_multistep_inference

app = FastAPI(title="Multi-Freq IoT Inference API")

loaded_models = {}
loaded_info = {}
loaded_env_prophets = {}  

sync_lock = threading.Lock()
LAST_SYNC_TIME = {} 
SYNC_COOLDOWN_SECONDS = 30.0  

# ---
# Helper Functions for VPD and formatting

def calculate_vpd(t_leaf: float, t_air: float, rh: float) -> float:
    """Calculates Vapor Pressure Deficit (VPD) in kPa."""
    def svp(t):
        return 0.61078 * np.exp((17.27 * t) / (t + 237.3))
    
    es_leaf = svp(t_leaf)
    ea_air = svp(t_air) * (rh / 100.0)
    return max(0.0, es_leaf - ea_air)

def format_series(timestamps: List[Any], values: List[float]) -> List[Dict[str, Any]]:
    """Homologates time series data to a standardized dictionary format."""
    return [{"timestamp": t.isoformat() if hasattr(t, 'isoformat') else t, "value": round(v, 4)} 
            for t, v in zip(timestamps, values)]

def get_soft_task(task_or_group: str) -> str:
    """Maps a task or group to its corresponding soft sensor task for leaf_temp imputation."""
    task_or_group = task_or_group.upper()
    if task_or_group in ['T1', 'T2', 'T3', 'A']: return 't1'
    if task_or_group in ['T4', 'T5', 'T6', 'B']: return 't4'
    return 't4' 

# ---

@app.on_event("startup")
def load_assets():
    if not os.path.exists(BASE_MODEL_DIR): return
    for freq_folder in os.listdir(BASE_MODEL_DIR):
        if not freq_folder.endswith('m'): continue
        freq_key = freq_folder.replace('m', '') 
        freq_path = os.path.join(BASE_MODEL_DIR, freq_folder)
        loaded_models[freq_key] = {}
        loaded_info[freq_key] = {}
        loaded_env_prophets[freq_key] = {}
        
        for task in TASKS.keys():
            best_dir = os.path.join(freq_path, task, "best_model")
            model_path = os.path.join(best_dir, "best_model.joblib")
            info_path = os.path.join(best_dir, "best_model_info.json")
            if os.path.exists(model_path):
                loaded_models[freq_key][task] = joblib.load(model_path)
                if os.path.exists(info_path):
                    with open(info_path, "r") as f:
                        loaded_info[freq_key][task] = json.load(f)
                print(f"[RAM {freq_folder}] Pipeline {task.upper()} loaded successfully!")
                
        env_dir = os.path.join(freq_path, "env_forecasters")
        if os.path.exists(env_dir):
            for feat in TASKS["t1"]["features"]:
                prophet_path = os.path.join(env_dir, f"prophet_{feat}.json")
                if os.path.exists(prophet_path):
                    with open(prophet_path, "r") as f:
                        loaded_env_prophets[freq_key][feat] = model_from_json(f.read())
            print(f"[RAM {freq_folder}] Environmental Prophet forecasters loaded successfully!")

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
    
    # 1. JIT Data Sync: Make sure the clean bucket is up to date with RAW
    with sync_lock:
        current_time = time.time()
        last_sync = LAST_SYNC_TIME.get(freq_minutes, 0.0)
        if current_time - last_sync > SYNC_COOLDOWN_SECONDS:
            try:
                sync_clean_bucket(INFLUX_URL, INFLUX_TOKEN, INFLUX_ORG, freq_minutes=freq_minutes)
                LAST_SYNC_TIME[freq_minutes] = time.time()
            except Exception as e:
                print(f"[API] Error during JIT Sync ({freq_minutes}m): {e}")

    # 2. Fetch required window
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
            
            # --- Inject 'is_indoor' so inference datasets match training datasets
            if USE_INDOOR_FEATURE:
                df['is_indoor'] = df['id_board'].map(BOARD_ENV_MAP).fillna(0).astype(int)
                
        return df
    except Exception as e:
        print(f"InfluxDB Query Error: {e}")
        return pd.DataFrame()

def _prepare_inference_context(freq_minutes: int, board_id: str, task_or_group: str, 
                               custom_data: Optional[SensorData] = None, 
                               use_real_leaf_temp: bool = False) -> tuple:
    """DRY Function to handle fetching, injecting, imputing leaf_temp, and Prophet prep."""
    freq_key = str(freq_minutes)
    fetch_latest, _ = get_fetch_limits(freq_minutes)
    df_history = fetch_historical_data(board_id, limit=fetch_latest, freq_minutes=freq_minutes)
    
    if len(df_history) < get_min_history_records(freq_minutes):
        raise HTTPException(status_code=400, detail=f"Insufficient historical data for board {board_id}.")

    # WHAT-IF Injection (Manual override)
    if custom_data:
        last_idx = df_history.index[-1]
        custom_values = custom_data.dict(exclude_none=True)
        for k, v in custom_values.items():
            if k in df_history.columns:
                df_history.loc[last_idx, k] = v

    # 3. Prevent Cheating / Missing Data: ALWAYS Impute Historical Leaf Temp using Soft Sensor (T1/T4)
    soft_task = get_soft_task(task_or_group)
    soft_model = loaded_models.get(freq_key, {}).get(soft_task)
    
    if not use_real_leaf_temp and soft_model:
        soft_config = TASKS[soft_task]
        virtual_ratio = get_virtual_ratio(freq_minutes)
        
        # --- FIX: INJECT is_indoor DYNAMICALLY ---
        soft_features = soft_config["features"].copy()
        if USE_INDOOR_FEATURE and 'is_indoor' not in soft_features:
            soft_features.append('is_indoor')
        # -----------------------------------------
        
        # Build advanced features properly
        df_history_adv = build_advanced_features(
            df_history, 
            soft_features, 
            soft_config.get("use_lags", False), 
            virtual_ratio
        )
        
        # Ensure we slice EXACTLY the features the soft model was trained on
        expected_features = list(soft_model.feature_names_in_)
        valid_idx = df_history_adv.dropna(subset=expected_features).index
        
        if not valid_idx.empty:
            # Overwrite the physical history with the calculated Soft Sensor data
            df_history.loc[valid_idx, 'leaf_temp'] = soft_model.predict(df_history_adv.loc[valid_idx, expected_features])
            # Fill remaining edges
            df_history['leaf_temp'] = df_history['leaf_temp'].ffill().bfill()

    # Calculate Historical VPD
    df_history['vpd'] = df_history.apply(
        lambda row: calculate_vpd(row.get('leaf_temp', 0), row.get('air_temp', 0), row.get('humidity', 0)), 
        axis=1
    )

    local_env_prophets = loaded_env_prophets.get(freq_key, {})

    return df_history, local_env_prophets

# API Routes

def _run_standard_inference(freq_minutes: int, task: str, board_id: str, 
                            custom_data: Optional[SensorData] = None, 
                            use_real_leaf_temp: bool = False):
    freq_key = str(freq_minutes)
    if freq_key not in loaded_models or task not in loaded_models[freq_key]:
        raise HTTPException(status_code=404, detail=f"Task {task} not configured or trained.")
        
    df_history, local_prophets = _prepare_inference_context(
        freq_minutes, board_id, task, custom_data, use_real_leaf_temp
    )

    pred_list = recursive_multistep_inference(
        T_current_data=df_history, prophet_models=local_prophets,
        ml_model_pipeline=loaded_models[freq_key][task],
        task_config=TASKS[task], freq_minutes=freq_minutes
    )
    
    last_timestamp = df_history.index[-1]
    future_timestamps = [last_timestamp + pd.Timedelta(minutes=freq_minutes * (i + 1)) for i in range(len(pred_list))]
    
    # Calculate Future VPD if predicting leaf_temp
    future_vpd = []
    if TASKS[task]["target"] == "leaf_temp" and len(pred_list) > 0:
        future_dates_naive = [t.tz_localize(None) if t.tz is not None else t for t in future_timestamps]
        df_future = pd.DataFrame({'ds': future_dates_naive})
        if USE_INDOOR_FEATURE and 'is_indoor' in df_history.columns:
            df_future['is_indoor'] = df_history['is_indoor'].iloc[-1]
            
        air_preds = local_prophets["air_temp"].predict(df_future)['yhat'].values
        hum_preds = local_prophets["humidity"].predict(df_future)['yhat'].values
        future_vpd = [calculate_vpd(lt, at, rh) for lt, at, rh in zip(pred_list, air_preds, hum_preds)]

    return {
        "task": task, 
        "frequency": f"{freq_minutes}m", 
        "target": TASKS[task]["target"],
        "historical": {
            "leaf_temp_estimated": format_series(df_history.index, df_history.get('leaf_temp', [])),
            "vpd_calculated": format_series(df_history.index, df_history.get('vpd', []))
        },
        "predictions": {
            "target_forecast": format_series(future_timestamps, pred_list),
            "vpd_forecast": format_series(future_timestamps, future_vpd) if future_vpd else None
        }
    }


@app.get("/predict/{freq_minutes}m/standard/{task}/latest")
def predict_latest(freq_minutes: int, task: str, board_id: str = DEFAULT_BOARD_ID, 
                   use_real_leaf_temp: bool = Query(False, description="Set True to use physical sensor historical data instead of Soft Sensor")):
    return _run_standard_inference(freq_minutes, task, board_id, None, use_real_leaf_temp)


@app.post("/predict/{freq_minutes}m/standard/{task}/manual")
def predict_manual(freq_minutes: int, task: str, custom_data: SensorData, board_id: str = DEFAULT_BOARD_ID,
                   use_real_leaf_temp: bool = Query(False, description="Set True to use physical sensor historical data instead of Soft Sensor")):
    return _run_standard_inference(freq_minutes, task, board_id, custom_data, use_real_leaf_temp)


#############


def _run_ensemble_inference(freq_minutes: int, group: str, board_id: str, 
                            custom_data: Optional[SensorData] = None, 
                            use_real_leaf_temp: bool = False):
    freq_key = str(freq_minutes)
    group = group.upper()
    if group == 'A': t_soft, t_env, t_auto = "t1", "t2", "t3"
    elif group == 'B': t_soft, t_env, t_auto = "t4", "t5", "t6"
    elif group == 'C': t_soft, t_env, t_auto = "t4", "t8", "t9"
    else: raise HTTPException(status_code=400, detail="Group must be 'A' or 'B'.")

    for t in [t_soft, t_env, t_auto]:
        if freq_key not in loaded_models or t not in loaded_models[freq_key]:
            raise HTTPException(status_code=404, detail="Incomplete models for this ensemble.")

    df_history, local_prophets = _prepare_inference_context(
        freq_minutes, board_id, group, custom_data, use_real_leaf_temp
    )

    ml_models = {"soft": loaded_models[freq_key][t_soft], "env": loaded_models[freq_key][t_env], "auto": loaded_models[freq_key][t_auto]}
    task_configs = {"soft": TASKS[t_soft], "env": TASKS[t_env], "auto": TASKS[t_auto]}

    # Extract MAE dynamically for weighted blending
    mae_env = loaded_info.get(freq_key, {}).get(t_env, {}).get("mae", 0.5) 
    mae_auto = loaded_info.get(freq_key, {}).get(t_auto, {}).get("mae", 0.5) 

    result = ensemble_multistep_inference(
        T_current_data=df_history, prophet_models=local_prophets, 
        ml_models=ml_models, task_configs=task_configs, 
        freq_minutes=freq_minutes, mae_env=mae_env, mae_auto=mae_auto
    )
    
    last_timestamp = df_history.index[-1]
    future_timestamps = [last_timestamp + pd.Timedelta(minutes=freq_minutes * (i + 1)) for i in range(len(result["forecast_blended"]))]
    
    # Calculate Future VPD based on blended prediction
    future_dates_naive = [t.tz_localize(None) if t.tz is not None else t for t in future_timestamps]
    df_future = pd.DataFrame({'ds': future_dates_naive})
    if USE_INDOOR_FEATURE and 'is_indoor' in df_history.columns:
        df_future['is_indoor'] = df_history['is_indoor'].iloc[-1]
        
    air_preds = local_prophets["air_temp"].predict(df_future)['yhat'].values
    hum_preds = local_prophets["humidity"].predict(df_future)['yhat'].values
    future_vpd = [calculate_vpd(lt, at, rh) for lt, at, rh in zip(result["forecast_blended"], air_preds, hum_preds)]

    mae_soft = loaded_info.get(freq_key, {}).get(t_soft, {}).get("mae", 1.0) 

    return {
        "group": group, 
        "frequency": f"{freq_minutes}m", 
        "soft_sensor_mae": round(mae_soft, 3),
        "historical": {
            "leaf_temp_estimated": format_series(df_history.index, df_history.get('leaf_temp', [])),
            "vpd_calculated": format_series(df_history.index, df_history.get('vpd', []))
        },
        "predictions": {
            "forecast_blended": format_series(future_timestamps, result["forecast_blended"]),
            "forecast_env": format_series(future_timestamps, result["forecast_env"]),
            "forecast_auto": format_series(future_timestamps, result["forecast_auto"]),
            "vpd_forecast": format_series(future_timestamps, future_vpd)
        },
        "prophet_projections": {
            "air_temp": format_series(future_timestamps, air_preds),
            "humidity": format_series(future_timestamps, hum_preds)
        }
    }


@app.get("/predict/{freq_minutes}m/ensemble/{group}/latest")
def predict_ensemble(freq_minutes: int, group: str = "B", board_id: str = DEFAULT_BOARD_ID,
                     use_real_leaf_temp: bool = Query(False, description="Set True to use physical sensor historical data instead of Soft Sensor")):
    return _run_ensemble_inference(freq_minutes, group, board_id, None, use_real_leaf_temp)


@app.post("/predict/{freq_minutes}m/ensemble/{group}/manual")
def predict_ensemble_manual(freq_minutes: int, group: str, custom_data: SensorData, board_id: str = DEFAULT_BOARD_ID,
                            use_real_leaf_temp: bool = Query(False, description="Set True to use physical sensor historical data instead of Soft Sensor")):
    return _run_ensemble_inference(freq_minutes, group, board_id, custom_data, use_real_leaf_temp)

################

@app.get("/info/{freq_minutes}m/{task}")
def get_task_info(freq_minutes: int, task: str):
    freq_key = str(freq_minutes)
    if freq_key not in loaded_info or task not in loaded_info[freq_key]:
        raise HTTPException(status_code=404, detail="Metrics not found.")
    return loaded_info[freq_key][task]


@app.post("/reload-models")
def reload_models():
    try:
        load_assets()
        return {"message": "Models successfully reloaded in RAM.", "status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {e}")