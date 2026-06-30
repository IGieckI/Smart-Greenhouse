import os
import sys
import json
import time
import pandas as pd
import numpy as np
import joblib
import matplotlib.pyplot as plt
import seaborn as sns

# Solo pulizia cache! La generazione avviene Lazy in API
from analytics_plotter import clear_analytics_cache

from influxdb_client import InfluxDBClient
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import TimeSeriesSplit, GridSearchCV

from sklearn.linear_model import Ridge
from sklearn.ensemble import RandomForestRegressor
from sklearn.svm import SVR
from lightgbm import LGBMRegressor
from prophet import Prophet
from prophet.serialize import model_to_json

from sklearn.pipeline import Pipeline
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import PolynomialFeatures

import cmdstanpy
import os        

try:
    os.environ['CMDSTAN'] = cmdstanpy.cmdstan_path()
except Exception as e:
    print(f"[Warning] Impossibile settare CMDSTAN dinamicamente: {e}")

sys.path.append('/app')
from shared_core.preprocessing import build_advanced_features, get_extended_features_list, create_lagged_features
from shared_core.config import *
from shared_core.tasks import TASKS
import traceback

# ==========================================
# DATA FETCHING
# ==========================================

def fetch_clean_data(freq_minutes: int):
    bucket_clean = f"{BUCKET_CLEAN_PREFIX}{freq_minutes}m"
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    query = f'''
        from(bucket: "{bucket_clean}")
          |> range(start: 0)
          |> filter(fn: (r) => r._measurement == "sensor_measurements")
          |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
    '''
    df = client.query_api().query_data_frame(query)
    if isinstance(df, list):
        if len(df) == 0: return pd.DataFrame()
        df = pd.concat(df, ignore_index=True)
        
    if not df.empty:
        df.set_index('_time', inplace=True)
        df.sort_index(inplace=True)
    return df

def fetch_raw_training_data():
    print("[Data] Fetching massive RAW dataset for point-wise tasks (T1/T4)...")
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    query = f'''
        from(bucket: "{BUCKET_RAW}")
          |> range(start: {SYNC_LOOKBACK_DAYS})
          |> filter(fn: (r) => r._measurement == "sensor_measurements")
          |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
    '''
    df = client.query_api().query_data_frame(query)
    if isinstance(df, list):
        if len(df) == 0: return pd.DataFrame()
        df = pd.concat(df, ignore_index=True)
        
    if not df.empty:
        df.set_index('_time', inplace=True)
        df.sort_index(inplace=True)
        if 'tds_value' in df.columns:
            if 'tds' in df.columns: df['tds'] = df['tds'].combine_first(df['tds_value'])
            else: df.rename(columns={'tds_value': 'tds'}, inplace=True)
            df.drop(columns=['tds_value'], inplace=True, errors='ignore')

        if 'leaf_temperature' in df.columns:
            if 'leaf_temp' in df.columns: df['leaf_temp'] = df['leaf_temp'].combine_first(df['leaf_temperature'])
            else: df.rename(columns={'leaf_temperature': 'leaf_temp'}, inplace=True)
            df.drop(columns=['leaf_temperature'], inplace=True, errors='ignore')
            
    return df

# ==========================================
# EVALUATION & LOGGING (No plotting qui, solo test veri contro environment)
# ==========================================
def log_and_evaluate(y_test, y_pred, features_names, model, model_name, task_name, training_time, inf_time, best_params, archive_dir):
    mae = mean_absolute_error(y_test, y_pred)
    rmse = np.sqrt(mean_squared_error(y_test, y_pred))
    r2 = r2_score(y_test, y_pred)

    if hasattr(model, 'named_steps') and 'poly_features' in model.named_steps:
        try: features_names = model.named_steps['poly_features'].get_feature_names_out()
        except: pass
    
    importance_dict = {}
    final_estimator = model.named_steps['regressor'] if hasattr(model, 'named_steps') else model

    if hasattr(final_estimator, 'feature_importances_'):
        importances = final_estimator.feature_importances_
        importance_dict = {feat: float(imp) for feat, imp in zip(features_names, importances)}
        importance_dict = dict(sorted(importance_dict.items(), key=lambda item: item[1], reverse=True))
    elif hasattr(final_estimator, 'coef_'):
        importances = np.ravel(final_estimator.coef_)
        importance_dict = {feat: float(imp) for feat, imp in zip(features_names, importances)}
        importance_dict = dict(sorted(importance_dict.items(), key=lambda item: np.abs(item[1]), reverse=True))

    report = {
        "model_name": model_name,
        "best_params": best_params,
        "metrics": {"MAE": round(mae, 3), "RMSE": round(rmse, 3), "R_squared": round(r2, 3)},
        "performance": {"training_time_seconds": round(training_time, 4), "inference_time_seconds": round(inf_time, 4)},
        "feature_importance": importance_dict
    }
    
    # --- MODIFICA: Scrittura atomica per evitare JSONDecodeError nell'API ---
    temp_file = os.path.join(archive_dir, f"{model_name}_metrics.tmp")
    final_file = os.path.join(archive_dir, f"{model_name}_metrics.json")
    
    with open(temp_file, "w") as f:
        json.dump(report, f, indent=4)
        
    os.rename(temp_file, final_file) # Operazione atomica garantita
    # ------------------------------------------------------------------------
        
    return report, mae

# ==========================================
# TRAINING PIPELINES
# ==========================================
def train_environmental_prophet(df_clean, features, output_dir, freq_minutes):
    print(f"\n{'='*60}\n[Trainer {freq_minutes}m] INDEPENDENT ENVIRONMENTAL PROPHET TRAINING\n{'='*60}")
    os.makedirs(output_dir, exist_ok=True)
    df_train = df_clean[df_clean['id_board'].isin(ACTIVE_BOARDS)].copy()
    
    effective_days = ENV_ARIMA_TRAIN_DAYS if freq_minutes >= 6 else 3
    tail_samples = int((effective_days * 24 * 60) / freq_minutes)
    
    for feat in features:
        try:
            print(f"Training Prophet for: {feat} (last {tail_samples} samples = {effective_days} days)...")
            if feat not in df_train.columns: continue

            y = df_train[feat].dropna().tail(tail_samples) 
            if len(y) < 100 or y.nunique() <= 1: continue
            
            timestamps = y.index.tz_localize(None) if y.index.tz is not None else y.index
            df_prophet_full = pd.DataFrame({
                'ds': pd.to_datetime(timestamps),
                'y': pd.to_numeric(y.values, errors='coerce')
            }).dropna()
            
            final_model = Prophet(
                daily_seasonality=True, 
                yearly_seasonality=False, 
                weekly_seasonality=False,
                stan_backend='CMDSTANPY'
            )
            final_model.fit(df_prophet_full)
            
            with open(os.path.join(output_dir, f"prophet_{feat}.json"), 'w') as fout:
                fout.write(model_to_json(final_model)) 
                
            print(f"-> Model for {feat} Saved & Evaluated")
            
        except Exception as e:
            print(f"-> [ERRORE PROPHET] Addestramento interrotto per {feat}: {str(e)}")
            continue

def get_model_grids(freq_minutes: int, poly_transformer: ColumnTransformer) -> dict:
    is_high_freq = freq_minutes < 6
    scaler_and_poly = [('poly_features', poly_transformer), ('scaler', MinMaxScaler())]
    scaler_only = [('scaler', MinMaxScaler())]

    if is_high_freq:
        return {
            "Ridge_linear": {
                "model": Pipeline(scaler_only + [('regressor', Ridge())]),
                "params": {"regressor__alpha": [0.1, 1.0, 10.0]} 
            },
            "LightGBM": {
                "model": Pipeline(scaler_only + [('regressor', LGBMRegressor(random_state=42, verbose=-1, n_jobs=1))]),
                "params": {
                    "regressor__n_estimators": [100, 300], 
                    "regressor__learning_rate": [0.05, 0.1],
                    "regressor__num_leaves": [31]
                }
            }
        }

    return {
        "Ridge_linear": {
            "model": Pipeline(scaler_only + [('regressor', Ridge())]),
            "params": {"regressor__alpha": [0.01, 0.1, 1.0, 10.0, 100.0]}
        },
        "Ridge_poly": { 
            "model": Pipeline(scaler_and_poly + [('regressor', Ridge())]),
            "params": {"regressor__alpha": [0.01, 0.1, 1.0, 10.0, 100.0]}
        },
        # "RandomForest": {
        #     "model": Pipeline(scaler_only + [('regressor', RandomForestRegressor(random_state=42, n_jobs=1))]),
        #     "params": {
        #         "regressor__n_estimators": [100, 300, 500], 
        #         "regressor__max_depth": [10, 20, None], 
        #         "regressor__min_samples_split": [2, 5, 10]
        #     }
        # },
        # "LightGBM": {
        #     "model": Pipeline(scaler_only + [('regressor', LGBMRegressor(random_state=42, verbose=-1, n_jobs=1))]),
        #     "params": {
        #         "regressor__n_estimators": [100, 300, 500],
        #         "regressor__learning_rate": [0.01, 0.05, 0.1],
        #         "regressor__num_leaves": [31, 63, 127]
        #     }
        # },
        # "SVR": {
        #     "model": Pipeline(scaler_only + [('regressor', SVR())]),
        #     "params": {
        #         "regressor__C": [0.1, 1.0, 10.0, 100.0], 
        #         "regressor__gamma": ["scale", "auto", 0.1, 0.01], 
        #         "regressor__kernel": ["linear", "rbf"],
        #         "regressor__epsilon": [0.000001, 0.0001, 0.01, 1]
        #     }
        # },
    }
def run_pipeline_for_task(task_name, config, df_data, freq_minutes, is_raw=False):
    print(f"\n{'='*60}\n[Trainer {freq_minutes}m] STARTING PIPELINE: {task_name.upper()} (RAW Data Mode: {is_raw})\n{'='*60}")
    
    target_col = config["target"]
    features_list = config["features"]
    use_lags = config.get("use_lags", False)
    lag_target = config.get("lag_target", True) 
    
    virtual_ratio = 1 if is_raw else get_virtual_ratio(freq_minutes)
    task_dir = os.path.join(BASE_MODEL_DIR, f"{freq_minutes}m", task_name)
    archive_dir, best_dir = [os.path.join(task_dir, p) for p in ["models_archive", "best_model"]]
    
    clear_analytics_cache(task_dir)
    for d in [archive_dir, best_dir]: os.makedirs(d, exist_ok=True)

    extended_features_list = get_extended_features_list(features_list, use_lags)
    
    # 1. Trovare il "Time Cutoff" globale per lo split (Garantisce allineamento temporale tra le board)
    global_min_time = df_data.index.min()
    global_max_time = df_data.index.max()
    total_duration = global_max_time - global_min_time
    split_time = global_min_time + (total_duration * TRAIN_SPLIT_PERCENTAGE)

    df_train_final_list, df_test_final_list = [], []

    for board_id in ACTIVE_BOARDS:
        df_b = df_data[df_data['id_board'] == board_id].copy()
        if df_b.empty: continue
        
        if is_raw:
            df_b = df_b.resample('1min').mean(numeric_only=True)
            df_b = df_b.ffill(limit=3)
            if board_id == BOARD_944 and 'tds' in df_b.columns: df_b.loc[df_b['tds'] < 60, 'tds'] = np.nan
            if 'water_temp' in df_b.columns: df_b.loc[df_b['water_temp'] < MIN_VALID_WATER_TEMP, 'water_temp'] = np.nan
        
        # Generazione Features e Lags (richiede continuità temporale)
        df_b = build_advanced_features(df_b, features_list, use_lags, virtual_ratio)
        if use_lags:
            df_b = create_lagged_features(df_b, target_col, extended_features_list, virtual_ratio, lags=DEFAULT_LAGS, lag_target=lag_target)

        if target_col not in df_b.columns:
            print(f"[{task_name}] Target '{target_col}' not found for board {board_id}. Skipping.")
            continue

        if use_lags:
            model_features = [col for col in df_b.columns if ('lag' in col and (lag_target or target_col not in col)) or col in extended_features_list]
        else:
            model_features = [col for col in extended_features_list if col in df_b.columns] 

        # 2. Split rigorosamente TEMPORALE (Nessuna asincronia tra board)
        df_train_b = df_b[df_b.index <= split_time].copy()
        df_test_b  = df_b[df_b.index > split_time].copy()

        # 3. Dropna DOPO lo split temporale per non falsare le proporzioni cronologiche
        df_train_b.dropna(subset=model_features + [target_col], inplace=True)
        df_test_b.dropna(subset=model_features + [target_col], inplace=True)

        if not df_train_b.empty: df_train_final_list.append(df_train_b)
        if not df_test_b.empty: df_test_final_list.append(df_test_b)

    if not df_train_final_list or not df_test_final_list:
        print(f"[{task_name}] Error: Empty datasets after temporal split/processing. Skipping.")
        return

    # 4. IL FIX CRUCIALE: NON usare sort_index() globale!
    # Ordiniamo prima per ID Board, in modo che il TimeSeriesSplit veda serie continue
    df_train_final = pd.concat(df_train_final_list)
    df_test_final = pd.concat(df_test_final_list)
    
    # Questo assicura che Tutta la board 1 venga prima della board 2
    df_train_final.sort_values(by=['id_board', '_time'], inplace=True)
    df_test_final.sort_values(by=['id_board', '_time'], inplace=True)

    print(f"[{task_name}] Final Train Vol: {len(df_train_final)} | Test Vol: {len(df_test_final)}")
    
    X_train, y_train = df_train_final[model_features], df_train_final[target_col]
    X_test, y_test = df_test_final[model_features], df_test_final[target_col]

    
    poly_transformer = ColumnTransformer(
        transformers=[('poly', PolynomialFeatures(degree=3, include_bias=False), features_list)],
        remainder='passthrough'
    )

    models_grids = get_model_grids(freq_minutes, poly_transformer)
    cv_splits = 2 if freq_minutes < 6 else 3
    tscv = TimeSeriesSplit(n_splits=cv_splits)
    
    best_overall_model = None
    best_overall_mae = float('inf')
    best_model_name = ""

    for name, config in models_grids.items():
        print(f"[{task_name}] Training {name}...")
        grid_search = GridSearchCV(estimator=config["model"], param_grid=config["params"], cv=tscv, scoring='neg_mean_absolute_error', n_jobs=-1)
        
        start_time = time.time()
        grid_search.fit(X_train, y_train) 
        training_time = time.time() - start_time

        best_model = grid_search.best_estimator_
        best_params = grid_search.best_params_
        
        start_inf = time.time()
        y_pred = best_model.predict(X_test) 
        inf_time = time.time() - start_inf

        report, mae = log_and_evaluate(
            y_test=y_test, y_pred=y_pred, features_names=model_features,
            model=best_model, model_name=name, task_name=task_name, training_time=training_time, 
            inf_time=inf_time, best_params=best_params, archive_dir=archive_dir
        )
        
        if mae < best_overall_mae:
            best_overall_mae = mae
            best_overall_model = best_model
            best_model_name = name

    print(f"\n[{task_name}] Best Model: {best_model_name} (MAE: {best_overall_mae:.3f})")
    joblib.dump(best_overall_model, os.path.join(best_dir, "best_model.joblib"))
    with open(os.path.join(best_dir, "best_model_info.json"), "w") as f:
        json.dump({"best_model": best_model_name, "mae": best_overall_mae, "target": target_col}, f)

# ==========================================
# MAIN EXECUTION
# ==========================================
def main():
    print("[Trainer] Starting Multi-Frequency Global Pipeline...")
    df_raw = fetch_raw_training_data()
    
    for freq in DEFAULT_FREQS:
        print(f"\n=== BEGIN TRAINING FOR FREQUENCY {freq} MINUTES ===")
        df_clean = fetch_clean_data(freq)
        
        if df_clean.empty:
            print(f"[Trainer] Insufficient 6m data. Please run cleaner.py first.")
            continue
        
        all_env_features = TASKS["t1"]["features"]
        env_output_dir = os.path.join(BASE_MODEL_DIR, f"{freq}m", "env_forecasters")
        train_environmental_prophet(df_clean, all_env_features, env_output_dir, freq)

        for task_name, config in TASKS.items():
            if config.get("use_lags", False):
                run_pipeline_for_task(task_name, config, df_clean, freq, is_raw=False)
            else:
                run_pipeline_for_task(task_name, config, df_raw, freq, is_raw=True)
                
    print(f"\n[Trainer] Pipeline completed successfully! Artifacts & Visualizations saved in {BASE_MODEL_DIR}.")

if __name__ == "__main__":
    main()