import os
import sys
import json
import joblib
import time
import threading
import numpy as np
import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler
from influxdb_client import InfluxDBClient
from prophet.serialize import model_from_json
from fastapi import FastAPI, HTTPException, Query
from typing import Optional, List, Dict, Any
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

sys.path.append('/app')
from shared_core.data_sync import sync_clean_bucket
from shared_core.tasks import TASKS, GROUPS, ENV_FEATURES
from shared_core.config import *
from shared_core.preprocessing import build_advanced_features
from .predictor import recursive_multistep_inference, ensemble_multistep_inference
from .sensor_payload import SensorData

app = FastAPI(title="Greenhouse IoT Inference API")

loaded_models = {}
loaded_info = {}
loaded_env_prophets = {}

sync_lock = threading.Lock()
LAST_SYNC_TIME = {} 
SYNC_COOLDOWN_SECONDS = 30.0  

def calculate_vpd(t_leaf: float, t_air: float, rh: float) -> float:
    def svp(t):
        return 0.61078 * np.exp((17.27 * t) / (t + 237.3))
    
    es_leaf = svp(t_leaf)
    ea_air = svp(t_air) * (rh / 100.0)
    return max(0.0, es_leaf - ea_air)

def format_series(timestamps: list, values: List[float]) -> List[Dict[str, Any]]:
    return [{"timestamp": t.isoformat() if hasattr(t, 'isoformat') else t, "value": round(v, 4)} 
                for t, v in zip(timestamps, values)]

def get_soft_task(task_or_group: str) -> str:
    task_or_group = task_or_group.upper()
    if task_or_group in ['T1', 'T2', 'T3', 'A']:
        return 't1'
    if task_or_group in ['T4', 'T5', 'T6', 'T8', 'T9', 'B' 'C']:
        return 't4'
    return 't4' 




def save_predictions_to_influx(board_id: str, freq_minutes: int, source_name: str, 
                               timestamps: list, values: list, 
                               air_preds: list = None, hum_preds: list = None):
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    write_api = client.write_api(write_options=SYNCHRONOUS)
    
    bucket_clean = f"{BUCKET_CLEAN_PREFIX}{freq_minutes}m"
    bucket_caveaux = BUCKET_CAVEAUX
    
    buckets_api = client.buckets_api()
    if buckets_api.find_bucket_by_name(bucket_caveaux) is None:
        buckets_api.create_bucket(bucket_name=bucket_caveaux, org=INFLUX_ORG)

    points = []
    for i, (t, v) in enumerate(zip(timestamps, values)):
        p = Point("sensor_measurements") \
            .tag("id_board", board_id) \
            .tag("model_source", source_name) \
            .tag("freq", f"{freq_minutes}m") \
            .field("leaf_temp_pred", float(v)) \
            .time(t.isoformat() if hasattr(t, 'isoformat') else t)
            
        if (air_preds is not None) and (i < len(air_preds)):
            p.field("air_temp_pred", float(air_preds[i]))
        if (hum_preds is not None) and (i < len(hum_preds)):
            p.field("humidity_pred", float(hum_preds[i]))

        points.append(p)

    if points:
        try:
            write_api.write(bucket=bucket_clean, org=INFLUX_ORG, record=points)
            write_api.write(bucket=bucket_caveaux, org=INFLUX_ORG, record=points)
            print(f"[Inference API] Saved {len(points)} points (Model: {source_name}) to {bucket_clean} and {bucket_caveaux}")
        except Exception as e:
            print(f"[Inference API] Prediction saving error: {e}")
    client.close()






def fetch_historical_data(board_id: str, limit: int, freq_minutes: int) -> pd.DataFrame:
    global LAST_SYNC_TIME
    
    with sync_lock:
        current_time = time.time()
        last_sync = LAST_SYNC_TIME.get(freq_minutes, 0.0)
        if current_time - last_sync > SYNC_COOLDOWN_SECONDS:
            try:
                sync_clean_bucket(INFLUX_URL, INFLUX_TOKEN, INFLUX_ORG, freq_minutes=freq_minutes)
                LAST_SYNC_TIME[freq_minutes] = time.time()
            except Exception as e:
                print(f"[API] Error during Sync ({freq_minutes}m): {e}")

    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    bucket_clean = f"{BUCKET_CLEAN_PREFIX}{freq_minutes}m"
    query = f'''
        from(bucket: "{bucket_clean}")
          |> range(start: {INFERENCE_LOOKBACK_DAYS})
          |> filter(fn: (r) => r._measurement == "sensor_measurements")
          |> filter(fn: (r) => r.id_board == "{board_id}")
          |> filter(fn: (r) => r._field !~ /pred/)
          |> drop(columns: ["model_source", "freq"])
          |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
          |> tail(n: {limit})
    '''
    try:
        df = client.query_api().query_data_frame(query)
        if isinstance(df, list):
            if len(df) == 0: 
                return pd.DataFrame()
            df = pd.concat(df, ignore_index=True)
        if not df.empty:
            df.set_index('_time', inplace=True)
            df.sort_index(inplace=True)
            
            df = df[~df.index.duplicated(keep='last')]
            if USE_INDOOR_FEATURE:
                df['is_indoor'] = df['id_board'].map(BOARD_ENV_MAP).fillna(0).astype(int)
            
        return df
    except Exception as e:
        print(f"InfluxDB Query Error: {e}")
        return pd.DataFrame()



def _prepare_inference_context(freq_minutes: int, board_id: str, task_or_group: str, 
                               custom_data: Optional[SensorData] = None, 
                               use_real_leaf_temp: bool = False) -> tuple:
    freq_key = str(freq_minutes)
    fetch_latest, _ = get_fetch_limits(freq_minutes)
    df_history = fetch_historical_data(board_id, limit=fetch_latest, freq_minutes=freq_minutes)
    
    if len(df_history) < get_min_history_records(freq_minutes):
        raise HTTPException(status_code=400, detail=f"Insufficient historical data for board {board_id}.")

    if custom_data is not None:
        last_idx = df_history.index[-1]
        custom_values = custom_data.dict(exclude_none=True)
        for k, v in custom_values.items():
            if k in df_history.columns:
                df_history.loc[last_idx, k] = v

    soft_task = get_soft_task(task_or_group)
    soft_model = loaded_models.get(freq_key, {}).get(soft_task)
    
    if (not use_real_leaf_temp) and (soft_model is not None):
        soft_config = TASKS[soft_task]
        
        soft_features = soft_config["features"].copy()
        if (USE_INDOOR_FEATURE) and ('is_indoor' not in soft_features):
            soft_features.append('is_indoor')
        
        df_history_adv = build_advanced_features(
            df_history, 
            soft_features, 
            soft_config.get("use_lags", False)
        )
        
        expected_features = list(soft_model.feature_names_in_)
        if (hasattr(soft_model, 'named_steps')) and ('drop_diff' in soft_model.named_steps):
             expected_features = [f for f in expected_features if not f.endswith('_diff')]

        valid_idx = df_history_adv.dropna(subset=expected_features).index
        
        if not valid_idx.empty:
            df_history.loc[valid_idx, 'leaf_temp'] = soft_model.predict(df_history_adv.loc[valid_idx, expected_features])
            df_history['leaf_temp'] = df_history['leaf_temp'].ffill().bfill()

    df_history['vpd'] = df_history.apply(
        lambda row: calculate_vpd(row.get('leaf_temp', 0), row.get('air_temp', 0), row.get('humidity', 0)), 
        axis=1
    )

    local_env_prophets = loaded_env_prophets.get(freq_key, {})

    return df_history, local_env_prophets



def scheduled_inference_job():
    print("[Scheduler] Starting hourly automatic inference...")
    for g in GROUPS:
        try:
            _run_ensemble_inference(freq_minutes=6, group=g, board_id=BOARD_324, custom_data=None, use_real_leaf_temp=False, save_to_db=True)
        except Exception as e:
            print(f"[Scheduler] Error running inference on group{g}: {e}")
    
    for t in TASKS:
        try:
            _run_ensemble_inference(freq_minutes=6, group=g, board_id=BOARD_324, custom_data=None, use_real_leaf_temp=False, save_to_db=True)
        except Exception as e:
            print(f"[Scheduler] Error running inference on task {t}: {e}")

def load_assets():
    if not os.path.exists(BASE_MODEL_DIR):
        return
    for freq_folder in os.listdir(BASE_MODEL_DIR):
        if not freq_folder.endswith('m'):
            continue
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
            loaded_feats = []
            missing_feats = []
            for feat in ENV_FEATURES:
                prophet_path = os.path.join(env_dir, f"prophet_{feat}.json")
                if os.path.exists(prophet_path):
                    with open(prophet_path, "r") as f:
                        loaded_env_prophets[freq_key][feat] = model_from_json(f.read())
                    loaded_feats.append(feat)
                else:
                    missing_feats.append(feat)
            
            if missing_feats:
                print(f"[RAM {freq_folder}] WARNING! Missing Prophet models for: {missing_feats}")
            print(f"[RAM {freq_folder}] Prophet models loaded for: {loaded_feats}")







@app.on_event("startup")
def start_scheduler():
    for mins in DEFAULT_FREQS:
        sync_clean_bucket(INFLUX_URL, INFLUX_TOKEN, INFLUX_ORG, freq_minutes=mins)

    load_assets()
    
    scheduler = BackgroundScheduler()
    scheduler.add_job(scheduled_inference_job, 'cron', minute=0)
    scheduler.start()

@app.get("/info/{freq_minutes}m/{task}")
def get_task_info(freq_minutes: int, task: str):
    freq_key = str(freq_minutes)
    if (freq_key not in loaded_info) or (task not in loaded_info[freq_key]):
        raise HTTPException(status_code=404, detail="Metrics not found.")
    return loaded_info[freq_key][task]

@app.post("/reload-models")
def reload_models():
    try:
        load_assets()
        return {"message": "Models successfully reloaded in RAM.", "status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {e}")





def formalize_return(summary_msg, freq_minutes, hist_leaf, fut_leaf, hist_air, hist_hum, fut_air, fut_hum, hist_vpd, fut_vpd):
    return {
        "message": summary_msg,
        "frequency": f"{freq_minutes}m",
        "leaf_temperature": {
            "historical": hist_leaf,
            "forecast": fut_leaf
        },
        "environmental_data": {
            "historical": {
                "air_temp": hist_air,
                "humidity": hist_hum
            },
            "forecast": {
                "air_temp": fut_air,
                "humidity": fut_hum
            }
        },
        "vpd": {
            "historical": hist_vpd,
            "forecast": fut_vpd
        }
    }



def _run_standard_inference(freq_minutes: int, task: str, board_id: str, 
                            custom_data: Optional[SensorData] = None, 
                            use_real_leaf_temp: bool = False,
                            save_to_db: bool = True):
    freq_key = str(freq_minutes)
    if (freq_key not in loaded_models) or (task not in loaded_models[freq_key]):
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
    
    future_vpd = []
    air_preds = []
    hum_preds = []

    if len(pred_list) > 0:
        future_dates_naive = [t.tz_localize(None) if t.tz is not None else t for t in future_timestamps]
        df_future = pd.DataFrame({'ds': future_dates_naive})
        if (USE_INDOOR_FEATURE) and ('is_indoor' in df_history.columns):
            df_future['is_indoor'] = df_history['is_indoor'].iloc[-1]
            
        if "air_temp" in local_prophets:
            air_preds = local_prophets["air_temp"].predict(df_future)['yhat'].values
        if "humidity" in local_prophets:
            hum_preds = local_prophets["humidity"].predict(df_future)['yhat'].values

        if (TASKS[task]["target"] == "leaf_temp" and len(air_preds) > 0) and (len(hum_preds) > 0):
            future_vpd = [calculate_vpd(lt, at, rh) for lt, at, rh in zip(pred_list, air_preds, hum_preds)]
    
        if save_to_db:
            save_predictions_to_influx(
                board_id, freq_minutes, task.upper(), future_timestamps, pred_list,
                air_preds if len(air_preds) > 0 else None,
                hum_preds if len(hum_preds) > 0 else None
            )

    hist_leaf = format_series(df_history.index, df_history.get('leaf_temp', []))
    hist_air = format_series(df_history.index, df_history.get('air_temp', []))
    hist_hum = format_series(df_history.index, df_history.get('humidity', []))
    hist_vpd = format_series(df_history.index, df_history.get('vpd', []))

    fut_leaf = format_series(future_timestamps, pred_list)
    fut_air = format_series(future_timestamps, air_preds) if len(air_preds) > 0 else []
    fut_hum = format_series(future_timestamps, hum_preds) if len(hum_preds) > 0 else []
    fut_vpd = format_series(future_timestamps, future_vpd) if len(future_vpd) > 0 else []

    summary_msg = f"Standard inference for task {task.upper()} on board {board_id} executed."
    
    if not save_to_db:
        summary_msg = f"What-if (manual) inference for task {task.upper()} on board {board_id} executed. Data not saved to InfluxDB."

    tmp = formalize_return(summary_msg, freq_minutes,
                           hist_leaf, fut_leaf, 
                           hist_air, hist_hum,
                           fut_air, fut_hum,
                           hist_vpd, fut_vpd)
    tmp["task"] = task
    return tmp

@app.get("/predict/{freq_minutes}m/standard/{task}/latest")
def predict_latest(freq_minutes: int, task: str, board_id: str = DEFAULT_BOARD_ID, 
                   use_real_leaf_temp: bool = Query(False, description="Set True to use physical sensor historical data instead of Soft Sensor")):
    return _run_standard_inference(freq_minutes, task, board_id, None, use_real_leaf_temp, save_to_db=True)

@app.post("/predict/{freq_minutes}m/standard/{task}/manual")
def predict_manual(freq_minutes: int, task: str, custom_data: SensorData, board_id: str = DEFAULT_BOARD_ID,
                   use_real_leaf_temp: bool = Query(False, description="Set True to use physical sensor historical data instead of Soft Sensor")):
    return _run_standard_inference(freq_minutes, task, board_id, custom_data, use_real_leaf_temp, save_to_db=False)





def _run_ensemble_inference(freq_minutes: int, group: str, board_id: str, 
                            custom_data: Optional[SensorData] = None, 
                            use_real_leaf_temp: bool = False,
                            save_to_db: bool = True):
    freq_key = str(freq_minutes)
    
    if not group:
        raise HTTPException(status_code=400, detail="Group identifier is required.")
        
    group = group.upper()
    
    if group not in GROUPS:
        possible_groups = ", ".join([f"'{g}'" for g in GROUPS])
        raise HTTPException(status_code=400, detail=f"Group must be one of these values: {possible_groups}.")
        
    group_targets = GROUPS[group]
    
    for t in group_targets:
        if (freq_key not in loaded_models) or (t not in loaded_models[freq_key]):
            raise HTTPException(status_code=404, detail="Incomplete models for this ensemble.")    
    
    t_soft, t_env, t_auto = group_targets

    df_history, local_prophets = _prepare_inference_context(
        freq_minutes, board_id, group, custom_data, use_real_leaf_temp
    )

    ml_models = {
        "soft": loaded_models[freq_key][t_soft],
        "env": loaded_models[freq_key][t_env],
        "auto": loaded_models[freq_key][t_auto]
    }
    
    task_configs = {
        "soft": TASKS[t_soft],
        "env": TASKS[t_env],
        "auto": TASKS[t_auto]
    }

    mae_env = loaded_info.get(freq_key, {}).get(t_env, {}).get("mae", -999.0) 
    mae_auto = loaded_info.get(freq_key, {}).get(t_auto, {}).get("mae", -999.0) 

    result = ensemble_multistep_inference(
        T_current_data=df_history, prophet_models=local_prophets, 
        ml_models=ml_models, task_configs=task_configs, 
        freq_minutes=freq_minutes, mae_env=mae_env, mae_auto=mae_auto
    )

    last_timestamp = df_history.index[-1]
    future_timestamps = [last_timestamp + pd.Timedelta(minutes=freq_minutes * (i + 1)) for i in range(len(result["forecast_blended"]))]
    
    future_dates_naive = [t.tz_localize(None) if t.tz is not None else t for t in future_timestamps]
    df_future = pd.DataFrame({'ds': future_dates_naive})
    if (USE_INDOOR_FEATURE) and ('is_indoor' in df_history.columns):
        df_future['is_indoor'] = df_history['is_indoor'].iloc[-1]
        
    air_preds = []
    hum_preds = []
    if "air_temp" in local_prophets:
        air_preds = local_prophets["air_temp"].predict(df_future)['yhat'].values
    if "humidity" in local_prophets:
        hum_preds = local_prophets["humidity"].predict(df_future)['yhat'].values

    future_vpd = []
    if (len(air_preds) > 0) and (len(hum_preds) > 0):
        future_vpd = [calculate_vpd(lt, at, rh) for lt, at, rh in zip(result["forecast_blended"], air_preds, hum_preds)]

    if (save_to_db) and (len(result["forecast_blended"]) > 0):
        save_predictions_to_influx(
            board_id, freq_minutes, f"ENSEMBLE_{group}", future_timestamps, result["forecast_blended"],
            air_preds if len(air_preds) > 0 else None,
            hum_preds if len(hum_preds) > 0 else None
        )

    hist_leaf = format_series(df_history.index, df_history.get('leaf_temp', []))
    hist_air = format_series(df_history.index, df_history.get('air_temp', []))
    hist_hum = format_series(df_history.index, df_history.get('humidity', []))
    hist_vpd = format_series(df_history.index, df_history.get('vpd', []))

    fut_leaf = format_series(future_timestamps, result["forecast_blended"])
    fut_air = format_series(future_timestamps, air_preds) if len(air_preds) > 0 else []
    fut_hum = format_series(future_timestamps, hum_preds) if len(hum_preds) > 0 else []
    fut_vpd = format_series(future_timestamps, future_vpd) if len(future_vpd) > 0 else []

    summary_msg = f"Ensemble inference for group {group.upper()} on board {board_id} executed."

    if not save_to_db:
        summary_msg = f"What-if (manual) ensemble inference for group {group.upper()} on board {board_id} executed. Data not saved to InfluxDB."

    tmp = formalize_return(summary_msg, freq_minutes,
                           hist_leaf, fut_leaf, 
                           hist_air, hist_hum,
                           fut_air, fut_hum,
                           hist_vpd, fut_vpd)
    tmp["group"] = group
    ens_det = {
            "weights": result.get("weights", {}),
            "forecast_env": format_series(future_timestamps, result.get("forecast_env", [])),
            "forecast_auto": format_series(future_timestamps, result.get("forecast_auto", []))
        }
    tmp["ensemble_details"] = ens_det
    return tmp

@app.get("/predict/{freq_minutes}m/ensemble/{group}/latest")
def predict_ensemble(freq_minutes: int, group: str = "B", board_id: str = DEFAULT_BOARD_ID,
                     use_real_leaf_temp: bool = Query(False, description="Set True to use physical sensor historical data instead of Soft Sensor")):
    return _run_ensemble_inference(freq_minutes, group, board_id, None, use_real_leaf_temp, save_to_db=True)

@app.post("/predict/{freq_minutes}m/ensemble/{group}/manual")
def predict_ensemble_manual(freq_minutes: int, group: str, custom_data: SensorData, board_id: str = DEFAULT_BOARD_ID,
                            use_real_leaf_temp: bool = Query(False, description="Set True to use physical sensor historical data instead of Soft Sensor")):
    return _run_ensemble_inference(freq_minutes, group, board_id, custom_data, use_real_leaf_temp, save_to_db=False)