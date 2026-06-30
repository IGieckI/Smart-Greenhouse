import os
import sys
import json
import time
import pandas as pd
import numpy as np
import joblib
import matplotlib.pyplot as plt
import seaborn as sns

# ELIMINA: from analytics_plotter import generate_analytics_plots
from analytics_plotter import clear_analytics_cache

from analytics_plotter import generate_analytics_plots

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

import cmdstanpy # <--- AGGIUNTO
import os        # <--- AGGIUNTO

# ==========================================
# FIX PROPHET: FORZA IL PATH DI CMDSTAN
# ==========================================
# Evita che Prophet usi la sua cartella "rotta" (stan_model) e lo forza 
# a puntare all'installazione globale appena compilata dal Dockerfile.
try:
    os.environ['CMDSTAN'] = cmdstanpy.cmdstan_path()
except Exception as e:
    print(f"[Warning] Impossibile settare CMDSTAN dinamicamente: {e}")

sys.path.append('/app')
from shared_core.preprocessing import build_advanced_features, get_extended_features_list, create_lagged_features

from shared_core.preprocessing import build_advanced_features, get_extended_features_list, create_lagged_features
from shared_core.config import *
from shared_core.tasks import TASKS

import traceback

# ==========================================
# DATA FETCHING
# ==========================================

def fetch_clean_data(freq_minutes: int):
    """Extracts the heavily preprocessed, 6-min regularized data for lagged forecasting."""
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
    """Extracts completely un-smoothed RAW data, preserving maximum data volume for T1/T4."""
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
        # Standardize TDS nomenclature
        if 'tds_value' in df.columns:
            if 'tds' in df.columns:
                df['tds'] = df['tds'].combine_first(df['tds_value'])
            else:
                df.rename(columns={'tds_value': 'tds'}, inplace=True)
            df.drop(columns=['tds_value'], inplace=True, errors='ignore')
    return df

# ==========================================
# PLOTTING & EVALUATION
# ==========================================

def plot_predictions(y_test, y_pred, model_name, mae, plots_dir, task_name):
    # 1. Full Test-Set Plot
    plt.figure(figsize=(12, 5))
    plt.plot(y_test.values, label='Actual True Values', color='green', alpha=0.6)
    plt.plot(y_pred, label=f'{model_name} Forecast', color='orange', alpha=0.8, linestyle='--')
    plt.title(f'[{task_name.upper()}] Full Performance: {model_name} (MAE: {mae:.3f})')
    plt.xlabel('Time Samples (Test Set)')
    plt.ylabel('Target Value')
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(plots_dir, f"{model_name}_full.png"))
    plt.close()

    # 2. Zoomed-In Plot (Last 150 points) for clearer variance evaluation
    zoom_size = min(150, len(y_test))
    y_test_zoom = y_test[-zoom_size:]
    y_pred_zoom = y_pred[-zoom_size:]
    
    plt.figure(figsize=(10, 4))
    plt.plot(y_test_zoom.values, label='Actual True Values', color='black', marker='o', markersize=3, alpha=0.7)
    plt.plot(y_pred_zoom, label=f'{model_name} Forecast', color='red', linestyle='--', marker='x', markersize=3)
    plt.title(f'[{task_name.upper()}] Zoomed Comparison (Last {zoom_size} pts)')
    plt.xlabel('Recent Time Samples')
    plt.ylabel('Target Value')
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(plots_dir, f"{model_name}_zoomed.png"))
    plt.close()

def plot_models_comparison(results_dict, plots_dir):
    names = list(results_dict.keys())
    maes = [res['MAE'] for res in results_dict.values()]
    plt.figure(figsize=(10, 6))
    bars = plt.barh(names, maes, color='skyblue')
    plt.xlabel('Mean Absolute Error (MAE) [Lower is better]')
    plt.title('Final Models Comparison')
    for bar in bars:
        plt.text(bar.get_width() + 0.05, bar.get_y() + bar.get_height()/2, f'{bar.get_width():.3f}', va='center')
    plt.tight_layout()
    plt.savefig(os.path.join(plots_dir, "final_models_comparison.png"))
    plt.close()

def generate_global_comparison_plot(df_clean, freq_minutes):
    """Evaluates all 6 tasks on a shared test window to visualize exactly where they fail/succeed."""
    print(f"\n[{freq_minutes}m] Generating Global Task Comparison Plot...")
    
    board_id = DEFAULT_BOARD_ID
    df_b = df_clean[df_clean['id_board'] == board_id].copy()
    if df_b.empty: return
    
    test_window = 300 # Approx 30 hours at 6m frequency
    history_needed = get_min_history_records(freq_minutes)
    
    # Slice the tail of the data, ensuring we have enough history to calculate lags safely
    df_slice = df_b.tail(test_window + history_needed).copy()
    
    plt.figure(figsize=(16, 8))
    true_target = df_slice['leaf_temp'].tail(test_window)
    plt.plot(true_target.index, true_target.values, label='True Leaf Temp', color='black', linewidth=3, zorder=10)
    
    colors = ['#FF3333', '#3333FF', '#33FF33', '#FF9933', '#9933FF', '#33FFFF']
    
    for idx, (task_name, config) in enumerate(TASKS.items()):
        model_path = os.path.join(BASE_MODEL_DIR, f"{freq_minutes}m", task_name, "best_model", "best_model.joblib")
        if not os.path.exists(model_path): continue
        
        model = joblib.load(model_path)
        use_lags = config.get("use_lags", False)
        lag_target = config.get("lag_target", True)
        features_list = config["features"]
        target_col = config["target"]
        virtual_ratio = get_virtual_ratio(freq_minutes)
        
        # Build features identically to Inference logic
        ext_features = get_extended_features_list(features_list, use_lags)
        df_feat = build_advanced_features(df_slice, features_list, use_lags, virtual_ratio)
        if use_lags:
            df_feat = create_lagged_features(df_feat, target_col, ext_features, virtual_ratio, lags=DEFAULT_LAGS, lag_target=lag_target)
            
        model_features = [col for col in df_feat.columns if ('lag' in col and (lag_target or target_col not in col)) or col in ext_features] if use_lags else ext_features
        
        # Predict purely on the exact test window slice
        df_infer = df_feat.tail(test_window)
        if not df_infer.empty and all(c in df_infer.columns for c in model_features):
            preds = model.predict(df_infer[model_features])
            plt.plot(df_infer.index, preds, label=f'Task {task_name.upper()}', color=colors[idx % len(colors)], alpha=0.8, linestyle='--')

    plt.title('Global Models Comparison: 6 Predictive Tasks vs True Environment', fontsize=14)
    plt.xlabel('Time (Last 30 Hours)')
    plt.ylabel('Leaf Temperature (°C)')
    plt.legend(loc='upper left', bbox_to_anchor=(1, 1))
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    
    plot_path = os.path.join(BASE_MODEL_DIR, f"{freq_minutes}m", "global_tasks_comparison.png")
    plt.savefig(plot_path)
    plt.close()
    print(f"[Plotting] Saved Global Comparison Matrix at {plot_path}")

def log_and_evaluate(y_test, y_pred, features_names, model, model_name, task_name, training_time, inf_time, best_params, archive_dir, plots_dir):
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
    
    with open(os.path.join(archive_dir, f"{model_name}_metrics.json"), "w") as f:
        json.dump(report, f, indent=4)
        
    plot_predictions(y_test, y_pred, model_name, mae, plots_dir, task_name)
    return report, mae

# ==========================================
# TRAINING PIPELINES
def train_environmental_prophet(df_clean, features, output_dir, freq_minutes):
    print(f"\n{'='*60}\n[Trainer {freq_minutes}m] INDEPENDENT ENVIRONMENTAL PROPHET TRAINING\n{'='*60}")
    os.makedirs(output_dir, exist_ok=True)
    df_train = df_clean[df_clean['id_board'].isin(ACTIVE_BOARDS)].copy()
    
    effective_days = ENV_ARIMA_TRAIN_DAYS if freq_minutes >= 6 else 3
    tail_samples = int((effective_days * 24 * 60) / freq_minutes)
    
    for feat in features:
        try:
            print(f"Training Prophet for: {feat} (last {tail_samples} samples = {effective_days} days)...")
            
            if feat not in df_train.columns:
                print(f"-> Feature {feat} non trovata. Addestramento saltato.")
                continue

            y = df_train[feat].dropna().tail(tail_samples) 
            
            if len(y) < 100:
                print(f"-> Dati insufficienti per {feat} ({len(y)} samples). Salto.")
                continue
                
            if y.nunique() <= 1:
                print(f"-> La serie di {feat} è piatta. Salto.")
                continue
            
            timestamps = y.index.tz_localize(None) if y.index.tz is not None else y.index
            zoom_size = min(150, len(y) // 5)
            
            if zoom_size > 0:
                train_ts, train_y = timestamps[:-zoom_size], y.values[:-zoom_size]
                test_ts, test_y = timestamps[-zoom_size:], y.values[-zoom_size:]
            else:
                train_ts, train_y = timestamps, y.values
                test_ts, test_y = timestamps[-1:], y.values[-1:] 
            
            # --- FIX 1: Cast FORZATO ai tipi supportati nativamente da Stan ---
            df_prophet_train = pd.DataFrame({
                'ds': pd.to_datetime(train_ts),
                'y': pd.to_numeric(train_y, errors='coerce')
            }).dropna()
            
            df_prophet_test = pd.DataFrame({
                'ds': pd.to_datetime(test_ts)
            })
            
            # Init e Fit
            model = Prophet(
                daily_seasonality=True, 
                yearly_seasonality=False, 
                weekly_seasonality=False,
                stan_backend='CMDSTANPY'
            )
            # Se il backend C++ deve fallire, fallirà qui!
            model.fit(df_prophet_train)
            
            forecast = model.predict(df_prophet_test)
            preds = forecast['yhat'].values
            
            plt.figure(figsize=(10, 4))
            plt.plot(test_ts, test_y, label='True Environment', color='black')
            plt.plot(test_ts, preds, label='Prophet Forecast', color='blue', linestyle='--')
            plt.title(f'Prophet Zoom Forecast: {feat} (vs True Data)')
            plt.legend()
            plt.grid(True, alpha=0.3)
            plt.tight_layout()
            plt.savefig(os.path.join(output_dir, f"prophet_{feat}_zoom.png"))
            plt.close()
            
            print(f"-> Re-fitting full model for {feat} to save latest state...")
            df_prophet_full = pd.DataFrame({
                'ds': pd.to_datetime(timestamps),
                'y': pd.to_numeric(y.values, errors='coerce')
            }).dropna()
            
            # <--- AGGIUNGI stan_backend ANCHE QUI
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
            # --- FIX 2: Stampiamo lo stack trace reale per capire perché Stan fallisce ---
            traceback.print_exc()
            continue

def get_model_grids(freq_minutes: int, poly_transformer: ColumnTransformer) -> dict:
    """Returns the appropriate hyperparameter grids based on frequency overhead."""
    is_high_freq = freq_minutes < 6
    scaler_and_poly = [('poly_features', poly_transformer), ('scaler', MinMaxScaler())]
    scaler_only = [('scaler', MinMaxScaler())]

    if is_high_freq:
        print(f"[{freq_minutes}m] High frequency detected. Using reduced GridSearch to save RAM/Time.")
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
        "Ridge_poly": { # Rinominato in poly per distinguerlo chiaramente nei grafici di confronto
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
        "LightGBM": {
            "model": Pipeline(scaler_only + [('regressor', LGBMRegressor(random_state=42, verbose=-1, n_jobs=1))]),
            "params": {
                "regressor__n_estimators": [100, 300, 500],
                "regressor__learning_rate": [0.01, 0.05, 0.1],
                "regressor__num_leaves": [31, 63, 127]
            }
        },
        "SVR": {
            "model": Pipeline(scaler_only + [('regressor', SVR())]),
            "params": {
                "regressor__C": [0.1, 1.0, 10.0, 100.0], 
                "regressor__gamma": ["scale", "auto", 0.1, 0.01], 
                "regressor__kernel": ["linear", "rbf"],
                "regressor__epsilon": [0.000001, 0.0001, 0.01, 1]
            }
        },
    }


def run_pipeline_for_task(task_name, config, df_data, freq_minutes, is_raw=False):
    print(f"\n{'='*60}\n[Trainer {freq_minutes}m] STARTING PIPELINE: {task_name.upper()} (RAW Data Mode: {is_raw})\n{'='*60}")

    target_col = config["target"]
    features_list = config["features"]
    use_lags = config.get("use_lags", False)
    lag_target = config.get("lag_target", True) 
    
    virtual_ratio = 1 if is_raw else get_virtual_ratio(freq_minutes)
    
    task_dir = os.path.join(BASE_MODEL_DIR, f"{freq_minutes}m", task_name)
    archive_dir, best_dir, plots_dir = [os.path.join(task_dir, p) for p in ["models_archive", "best_model", "plots"]]
    
    for d in [archive_dir, best_dir, plots_dir]: 
        os.makedirs(d, exist_ok=True)

    extended_features_list = get_extended_features_list(features_list, use_lags)
    df_train_final_list, df_test_final_list = [], []

    for board_id in ACTIVE_BOARDS:
        df_b = df_data[df_data['id_board'] == board_id].copy()
        if df_b.empty: continue
        
        if is_raw:
            # RAW DENSE ALIGNMENT: Align jittery raw ms timestamps to a 1-minute grid and ffill slightly.
            # This preserves thousands of point-wise rows that would otherwise be destroyed by dropna().
            df_b = df_b.resample('1min').mean(numeric_only=True)
            df_b = df_b.ffill(limit=3)
            
            # Basic anomaly bounding without interpolation
            if board_id == BOARD_944 and 'tds' in df_b.columns:
                df_b.loc[df_b['tds'] < 60, 'tds'] = np.nan
            if 'water_temp' in df_b.columns:
                df_b.loc[df_b['water_temp'] < MIN_VALID_WATER_TEMP, 'water_temp'] = np.nan
        
        split_idx = int(len(df_b) * TRAIN_SPLIT_PERCENTAGE)
        df_train_b, df_test_b = df_b.iloc[:split_idx], df_b.iloc[split_idx:]

        df_train_b = build_advanced_features(df_train_b, features_list, use_lags, virtual_ratio)
        df_test_b = build_advanced_features(df_test_b, features_list, use_lags, virtual_ratio)

        if use_lags:
            df_train_b = create_lagged_features(df_train_b, target_col, extended_features_list, virtual_ratio, lags=DEFAULT_LAGS, lag_target=lag_target)
            df_test_b = create_lagged_features(df_test_b, target_col, extended_features_list, virtual_ratio, lags=DEFAULT_LAGS, lag_target=lag_target)

        df_train_final_list.append(df_train_b)
        df_test_final_list.append(df_test_b)

    df_train_final = pd.concat(df_train_final_list).sort_index()
    df_test_final = pd.concat(df_test_final_list).sort_index()

    if use_lags:
        model_features = [col for col in df_train_final.columns if ('lag' in col and (lag_target or target_col not in col)) or col in extended_features_list]
    else:
        model_features = extended_features_list 

    # Drop NaNs: Extremely strict to prevent dirty training.
    df_train_final.dropna(subset=model_features + [target_col], inplace=True)
    df_test_final.dropna(subset=model_features + [target_col], inplace=True)

    if df_train_final.empty or df_test_final.empty:
        print(f"[{task_name}] Error: Empty datasets after processing. Skipping.")
        return

    print(f"[{task_name}] Final Training Volume: {len(df_train_final)} points.")

    X_train, y_train = df_train_final[model_features], df_train_final[target_col]
    X_test, y_test = df_test_final[model_features], df_test_final[target_col]

    poly_transformer = ColumnTransformer(
        transformers=[('poly', PolynomialFeatures(degree=3, include_bias=False), features_list)],
        remainder='passthrough'
    )

    models_grids = get_model_grids(freq_minutes, poly_transformer)
    cv_splits = 2 if freq_minutes < 6 else 3
    tscv = TimeSeriesSplit(n_splits=cv_splits)
    
    results = {}
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
            inf_time=inf_time, best_params=best_params, archive_dir=archive_dir, plots_dir=plots_dir
        )
        results[name] = report["metrics"]
        
        if mae < best_overall_mae:
            best_overall_mae = mae
            best_overall_model = best_model
            best_model_name = name

    print(f"\n[{task_name}] Best Model: {best_model_name} (MAE: {best_overall_mae:.3f})")
    joblib.dump(best_overall_model, os.path.join(best_dir, "best_model.joblib"))
    with open(os.path.join(best_dir, "best_model_info.json"), "w") as f:
        json.dump({"best_model": best_model_name, "mae": best_overall_mae, "target": target_col}, f)
    
    formatted_results = {name: {"MAE": res["MAE"]} for name, res in results.items()}
    plot_models_comparison(formatted_results, plots_dir)
    # AGGIUNGI QUESTA RIGA QUI: Genera i plot analitici usando i JSON appena salvati
    generate_analytics_plots(task_dir, task_name)

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
        
        # 1. Environmental Prophet Forecasters
        all_env_features = TASKS["t1"]["features"]
        env_output_dir = os.path.join(BASE_MODEL_DIR, f"{freq}m", "env_forecasters")
        train_environmental_prophet(df_clean, all_env_features, env_output_dir, freq)

        # 2. ML Pipelines (Dual Strategy)
        for task_name, config in TASKS.items():
            if config.get("use_lags", False):
                # Lagged tasks require regularized 6m grid
                run_pipeline_for_task(task_name, config, df_clean, freq, is_raw=False)
            else:
                # Point-wise tasks (T1/T4) devour raw unstructured data
                run_pipeline_for_task(task_name, config, df_raw, freq, is_raw=True)
                
        # 3. Final Global Plotting Evaluation
        generate_global_comparison_plot(df_clean, freq)
            
    print(f"\n[Trainer] Pipeline completed successfully! Artifacts & Visualizations saved in {BASE_MODEL_DIR}.")

if __name__ == "__main__":
    main()