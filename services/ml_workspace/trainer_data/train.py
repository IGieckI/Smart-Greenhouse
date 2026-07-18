import os
import sys
import json
import time
import pandas as pd
import numpy as np
import joblib
import matplotlib.pyplot as plt
from influxdb_client import InfluxDBClient
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import TimeSeriesSplit, GridSearchCV
from sklearn.linear_model import Ridge
from sklearn.ensemble import RandomForestRegressor
from sklearn.svm import SVR
from lightgbm import LGBMRegressor
from sklearn.pipeline import Pipeline
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import PolynomialFeatures
from prophet import Prophet
from prophet.serialize import model_to_json

sys.path.append('/app')
from shared_core.preprocessing import build_advanced_features, get_extended_features_list, create_lagged_features
from shared_core.config import *
from shared_core.tasks import TASKS

def fetch_clean_data(freq_minutes: int):
    print(f"[Data Fetch] Pulling clean data from bucket for {freq_minutes}m frequency...")
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
        if len(df) == 0: 
            print(f"[Data Fetch] Warning: No data found in bucket {bucket_clean}.")
            return pd.DataFrame()
        df = pd.concat(df, ignore_index=True)
        
    if not df.empty:
        df.set_index('_time', inplace=True)
        df.sort_index(inplace=True)
        
        if USE_INDOOR_FEATURE:
            df['is_indoor'] = df['id_board'].map(BOARD_ENV_MAP).fillna(0).astype(int)
            print(f"[Data Fetch] Injected 'is_indoor' environmental toggle flag.")
            
        print(f"[Data Fetch] Successfully retrieved {len(df)} records.")
    return df




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
        "metrics": {
            "MAE": round(mae, 3),
            "RMSE": round(rmse, 3),
            "R_squared": round(r2, 3)
        },
        "performance": {
            "training_time_seconds": round(training_time, 4),
            "inference_time_seconds": round(inf_time, 4)
        },
        "feature_importance": importance_dict
    }
    
    temp_file = os.path.join(archive_dir, f"{model_name}_metrics.tmp")
    final_file = os.path.join(archive_dir, f"{model_name}_metrics.json")
    
    with open(temp_file, "w") as f:
        json.dump(report, f, indent=4)
        
    os.rename(temp_file, final_file)
        
    return report, mae

def train_environmental_prophet(df_clean, features, output_dir, freq_minutes):
    print(f"\n{'='*60}\n[Trainer {freq_minutes}m] INDEPENDENT ENVIRONMENTAL PROPHET TRAINING\n{'='*60}")
    os.makedirs(output_dir, exist_ok=True)
    df_train = df_clean[df_clean['id_board'].isin(ACTIVE_BOARDS)].copy()
    
    effective_days = ENV_MODELS_TRAIN_DAYS if freq_minutes >= 6 else 3
    tail_samples = int((effective_days * 24 * 60) / freq_minutes)
    print(f"[Prophet Setup] Target history per board: {effective_days} days ({tail_samples} tail samples).")
    
    for feat in features:
        try:
            print(f"[Prophet Training] Constructing Panel Dataset for feature: '{feat}'...")
            
            df_prophet_full = pd.DataFrame()
            for b_id in ACTIVE_BOARDS:
                df_b = df_train[df_train['id_board'] == b_id]
                if feat not in df_b.columns:
                    continue
                
                cols_to_extract = [feat]
                if USE_INDOOR_FEATURE and 'is_indoor' in df_b.columns:
                    cols_to_extract.append('is_indoor')
                    
                df_b = df_b[cols_to_extract].dropna().tail(tail_samples)
                if len(df_b) < 100 or df_b[feat].nunique() <= 1:
                    continue
                
                timestamps = df_b.index.tz_localize(None) if df_b.index.tz is not None else df_b.index
                tmp = pd.DataFrame({
                    'ds': pd.to_datetime(timestamps),
                    'y': pd.to_numeric(df_b[feat].values, errors='coerce')
                })
                if USE_INDOOR_FEATURE and 'is_indoor' in df_b.columns:
                    tmp['is_indoor'] = df_b['is_indoor'].values
                
                df_prophet_full = pd.concat([df_prophet_full, tmp], ignore_index=True)
                
            if df_prophet_full.empty: 
                print(f"[Prophet Warning] Not enough data accumulated for '{feat}'. Skipping.")
                continue

            df_prophet_full.dropna(inplace=True)
            df_prophet_full.sort_values('ds', inplace=True) 
            
            split_time = df_prophet_full['ds'].quantile(0.8)
            df_train_prophet = df_prophet_full[df_prophet_full['ds'] <= split_time]
            df_test_prophet = df_prophet_full[df_prophet_full['ds'] > split_time]
            
            print(f"[Prophet - {feat}] Temporal 80/20 Split | Train: {len(df_train_prophet)} | Test: {len(df_test_prophet)}")
            
            final_model = Prophet(daily_seasonality=True, yearly_seasonality=False, weekly_seasonality=False)
            if USE_INDOOR_FEATURE and 'is_indoor' in df_train_prophet.columns:
                final_model.add_regressor('is_indoor')
                print(f"[Prophet - {feat}] Using 'is_indoor' as an Extra Regressor.")
            
            final_model.fit(df_train_prophet)
            
            print(f"[Prophet - {feat}] Generating forecast for evaluation...")
            future = df_test_prophet[['ds']].copy()
            if USE_INDOOR_FEATURE and 'is_indoor' in df_test_prophet.columns:
                future['is_indoor'] = df_test_prophet['is_indoor'].values
                
            forecast = final_model.predict(future)
            mae = mean_absolute_error(df_test_prophet['y'], forecast['yhat'])
            rmse = np.sqrt(mean_squared_error(df_test_prophet['y'], forecast['yhat']))
            
            print(f"[Prophet - {feat}] Evaluation Completed -> MAE: {mae:.2f}, RMSE: {rmse:.2f}")

            plt.figure(figsize=(12, 6))
            plt.scatter(df_train_prophet['ds'], df_train_prophet['y'], label='Train Data', color='blue', alpha=0.3, s=5)
            plt.scatter(df_test_prophet['ds'], df_test_prophet['y'], label='Test Actual', color='black', alpha=0.5, s=5)
            plt.scatter(forecast['ds'], forecast['yhat'], label='Forecast', color='red', s=5)
            plt.title(f'Prophet Global Forecast vs Actuals: {feat.upper()} ({freq_minutes}m)')
            plt.legend()
            plt.grid(True)
            plt.savefig(os.path.join(output_dir, f"prophet_plot_{feat}.png"))
            plt.close()

            print(f"[Prophet - {feat}] Refitting on 100% of available data for production...")
            prod_model = Prophet(daily_seasonality=True, yearly_seasonality=False, weekly_seasonality=False)
            if USE_INDOOR_FEATURE and 'is_indoor' in df_prophet_full.columns:
                prod_model.add_regressor('is_indoor')
            prod_model.fit(df_prophet_full)

            with open(os.path.join(output_dir, f"prophet_{feat}.json"), 'w') as fout:
                fout.write(model_to_json(prod_model)) 
            with open(os.path.join(output_dir, f"prophet_metrics_{feat}.json"), 'w') as fout:
                json.dump({"MAE": mae, "RMSE": rmse}, fout)
            
            print(f"[Prophet - {feat}] Target successfully completed and saved.")
                
        except Exception as e:
            print(f"-> [PROPHET ERROR] Training interrupted for {feat}: {str(e)}")
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
        #         "regressor__n_estimators": [100, 300], 
        #         "regressor__max_depth": [10, 20, None], 
        #         "regressor__min_samples_split": [2, 5, 10],
        #         "regressor__max_features": ["sqrt", 1.0]
        #     }
        # },
        # "LightGBM": {
        #     "model": Pipeline(scaler_only + [('regressor', LGBMRegressor(random_state=42, verbose=-1, n_jobs=1))]),
        #     "params": {
        #         "regressor__n_estimators": [100, 300],
        #         "regressor__learning_rate": [0.01, 0.05, 0.1],
        #         "regressor__num_leaves": [31, 63],
        #         "regressor__subsample": [0.8, 1.0] 
        #     }
        # },
        # "SVR": {
        #     "model": Pipeline(scaler_only + [('regressor', SVR())]),
        #     "params": [
        #         {
        #             "regressor__kernel": ["linear"],
        #             "regressor__C": [0.1, 1.0, 10.0],
        #             "regressor__epsilon": [0.001, 0.01, 0.1]
        #         },
        #         {
        #             "regressor__kernel": ["rbf"],
        #             "regressor__C": [0.1, 1.0, 10.0],
        #             "regressor__gamma": ["scale", 0.1, 0.01], 
        #             "regressor__epsilon": [0.001, 0.01, 0.1]
        #         }
        #     ]
        # },
    }




def run_pipeline_for_task(task_name, config, df_data, freq_minutes):
    print(f"\n{'='*60}\n[Trainer {freq_minutes}m] STARTING PIPELINE: {task_name.upper()}\n{'='*60}")
    
    target_col = config["target"]
    
    features_list = config["features"].copy() 
    if USE_INDOOR_FEATURE and 'is_indoor' not in features_list:
        features_list.append('is_indoor')
        
    use_lags = config.get("use_lags", False)
    lag_target = config.get("lag_target", True) 
    task_lags = config.get("lags", DEFAULT_LAGS)

    task_dir = os.path.join(BASE_MODEL_DIR, f"{freq_minutes}m", task_name)
    archive_dir, best_dir = [os.path.join(task_dir, p) for p in ["models_archive", "best_model"]]
    
    for d in [archive_dir, best_dir]: 
        os.makedirs(d, exist_ok=True)

    extended_features_list = get_extended_features_list(features_list, use_lags)
    df_train_final_list, df_test_final_list = [], []

    print(f"[{task_name}] Target: '{target_col}' | Explicit Features Required: {features_list}")
    print(f"[{task_name}] Lags Enabled: {use_lags} | Lag Target: {lag_target}")

    for board_id in ACTIVE_BOARDS:
        df_b = df_data[df_data['id_board'] == board_id].copy()
        if df_b.empty: 
            print(f"[{task_name}] Warning: Board {board_id} dataset is empty. Skipping.")
            continue
        
        initial_len = len(df_b)
        print(f"[{task_name}] Processing Board {board_id} (Initial Clean Rows: {initial_len})")

        cols_to_keep = features_list + [target_col]
        available_cols = [c for c in cols_to_keep if c in df_b.columns]
        
        missing_cols = set(cols_to_keep) - set(available_cols)
        if missing_cols:
            print(f"[{task_name}] Warning: Board {board_id} is missing expected columns: {missing_cols}")
        
        df_b = df_b[available_cols].copy()
        print(f"[{task_name}] Board {board_id}: Isolated {len(available_cols)} relevant columns. Dropped all unnecessary features.")

        if target_col not in df_b.columns:
            print(f"[{task_name}] Target '{target_col}' not found for board {board_id}. Skipping.")
            continue

        df_b = build_advanced_features(df_b, features_list, use_lags)
        if use_lags:
            print(f"[{task_name}] Generating lags (Depth: {task_lags}) for Board {board_id}...")
            df_b = create_lagged_features(df_b, target_col, extended_features_list, lags=task_lags, lag_target=lag_target)

        if use_lags:
            model_features = [col for col in df_b.columns if ('lag' in col and (lag_target or target_col not in col)) or col in extended_features_list]
        else:
            model_features = [col for col in extended_features_list if col in df_b.columns] 

        pre_drop_len = len(df_b)
        df_b.dropna(subset=model_features + [target_col], inplace=True)
        dropped_rows = pre_drop_len - len(df_b)
        print(f"[{task_name}] NaN Filtration for Board {board_id}: Removed {dropped_rows} rows due to incomplete temporal context.")

        if df_b.empty: 
            print(f"[{task_name}] Board {board_id} dataset became empty after dropping NaNs. Skipping.")
            continue

        df_b['id_board'] = board_id

        split_idx = int(len(df_b) * TRAIN_SPLIT_PERCENTAGE)
        df_train_b, df_test_b = df_b.iloc[:split_idx], df_b.iloc[split_idx:]

        df_train_final_list.append(df_train_b)
        df_test_final_list.append(df_test_b)

    if not df_train_final_list or not df_test_final_list:
        print(f"[{task_name}] Error: Empty datasets after processing all boards. Skipping task.")
        return

    df_train_final = pd.concat(df_train_final_list).sort_values(by=['id_board', '_time'])
    df_test_final = pd.concat(df_test_final_list).sort_values(by=['id_board', '_time'])

    print(f"[{task_name}] Aggregation Complete | Final Valid Train Vol: {len(df_train_final)} | Test Vol: {len(df_test_final)}")
    
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
        print(f"[{task_name}] Initiating GridSearchCV Training for {name}...")
        grid_search = GridSearchCV(estimator=config["model"], param_grid=config["params"], cv=tscv, scoring='neg_mean_absolute_error', n_jobs=-1)
        
        start_time = time.time()
        grid_search.fit(X_train, y_train) 
        training_time = time.time() - start_time
        print(f"[{task_name}] -> {name} Training finished in {training_time:.2f}s")

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
        print(f"[{task_name}] -> {name} Evaluation | MAE: {mae:.3f} | Inference Time: {inf_time:.4f}s")
        
        if mae < best_overall_mae:
            best_overall_mae = mae
            best_overall_model = best_model
            best_model_name = name

    print(f"\n[{task_name}] Winner Selected: {best_model_name} (MAE: {best_overall_mae:.3f})")
    joblib.dump(best_overall_model, os.path.join(best_dir, "best_model.joblib"))
    with open(os.path.join(best_dir, "best_model_info.json"), "w") as f:
        json.dump({"best_model": best_model_name, "mae": best_overall_mae, "target": target_col}, f)