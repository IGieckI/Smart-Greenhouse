import os
import sys
import json
import time
import pandas as pd
import numpy as np
import joblib
import matplotlib.pyplot as plt
import seaborn as sns

from influxdb_client import InfluxDBClient
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import TimeSeriesSplit, GridSearchCV

from sklearn.linear_model import Ridge
from sklearn.ensemble import RandomForestRegressor
from sklearn.svm import SVR
from lightgbm import LGBMRegressor
import pmdarima as pm  

from sklearn.pipeline import Pipeline
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import PolynomialFeatures

sys.path.append('/app')
from shared_core.preprocessing import build_advanced_features, get_extended_features_list, create_lagged_features
from shared_core.config import *
from shared_core.tasks import TASKS


def fetch_clean_data(freq_minutes: int):
    """Extracts data from the dynamic bucket based on frequency."""
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

def plot_predictions(y_test, y_pred, model_name, mae, plots_dir):
    plt.figure(figsize=(12, 5))
    plt.plot(y_test.values, label='Actual Values', color='green', alpha=0.7)
    plt.plot(y_pred, label=f'{model_name} Predictions', color='orange', alpha=0.8, linestyle='--')
    plt.title(f'{model_name} Performance (MAE: {mae:.3f})')
    plt.xlabel('Time Samples (Test Set)')
    plt.ylabel('Target Value')
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(plots_dir, f"{model_name}_predictions.png"))
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

def log_and_evaluate(y_test, y_pred, features_names, model, model_name, training_time, inf_time, best_params, archive_dir, plots_dir):
    mae = mean_absolute_error(y_test, y_pred)
    rmse = np.sqrt(mean_squared_error(y_test, y_pred))
    r2 = r2_score(y_test, y_pred)

    if hasattr(model, 'named_steps') and 'poly_features' in model.named_steps:
        try:
            features_names = model.named_steps['poly_features'].get_feature_names_out()
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
        
    plot_predictions(y_test, y_pred, model_name, mae, plots_dir)
    return report, mae

def train_environmental_arimas(df_clean, features, output_dir, freq_minutes):
    print(f"\n{'='*60}\n[Trainer {freq_minutes}m] INDEPENDENT ENVIRONMENTAL ARIMA TRAINING\n{'='*60}")
    os.makedirs(output_dir, exist_ok=True)
    df_train = df_clean[df_clean['id_board'].isin(ACTIVE_BOARDS)].copy()
    
    # MITIGATION STRATEGY:
    # If freq < 6 min, ARIMA slows down drastically with too many samples. Reduce window to 3 days.
    effective_days = ENV_ARIMA_TRAIN_DAYS if freq_minutes >= 6 else 3
    tail_samples = int((effective_days * 24 * 60) / freq_minutes)
    
    for feat in features:
        print(f"Training for: {feat} (last {tail_samples} samples = {effective_days} days)...")
        y = df_train[feat].dropna().tail(tail_samples) 
        
        # Limit max iterations for high frequencies
        max_p_q = 5 if freq_minutes >= 6 else 3 
        
        best_model = pm.auto_arima(
            y, seasonal=False, stepwise=True, suppress_warnings=True,
            max_p=max_p_q, max_q=max_p_q
        )
        print(f"-> Optimal for {feat}: {best_model.order}")
        joblib.dump(best_model, os.path.join(output_dir, f"arima_{feat}.joblib"))
        
    print("Environmental models saved successfully.")

def get_model_grids(freq_minutes: int, poly_transformer: ColumnTransformer) -> dict:
    """Returns the appropriate hyperparameter grids based on frequency overhead."""
    is_high_freq = freq_minutes < 6
    scaler_and_poly = [('poly_features', poly_transformer), ('scaler', MinMaxScaler())]

    if is_high_freq:
        print(f"[{freq_minutes}m] High frequency detected. Using reduced GridSearch to save RAM/Time.")
        return {
            "Ridge": {
                "model": Pipeline(scaler_and_poly + [('regressor', Ridge())]),
                "params": {"regressor__alpha": [0.1, 1.0, 10.0]} 
            },
            "LightGBM": {
                "model": Pipeline([('scaler', MinMaxScaler()), ('regressor', LGBMRegressor(random_state=42, verbose=-1, n_jobs=1))]),
                "params": {
                    "regressor__n_estimators": [100, 300], 
                    "regressor__learning_rate": [0.05, 0.1],
                    "regressor__num_leaves": [31]
                }
            }
            # Note: SVR and RF removed for high frequency due to exorbitant execution times/RAM scaling.
        }

    return {
        "Ridge": {
            "model": Pipeline(scaler_and_poly + [('regressor', Ridge())]),
            "params": {"regressor__alpha": [0.01, 0.1, 1.0, 10.0, 100.0]}
        },
        "RandomForest": {
            "model": Pipeline([('scaler', MinMaxScaler()), ('regressor', RandomForestRegressor(random_state=42, n_jobs=1))]),
            "params": {
                "regressor__n_estimators": [100, 300, 500], 
                "regressor__max_depth": [10, 20, None], 
                "regressor__min_samples_split": [2, 5, 10]
            }
        },
        "LightGBM": {
            "model": Pipeline([('scaler', MinMaxScaler()), ('regressor', LGBMRegressor(random_state=42, verbose=-1, n_jobs=1))]),
            "params": {
                "regressor__n_estimators": [100, 300, 500],
                "regressor__learning_rate": [0.01, 0.05, 0.1],
                "regressor__num_leaves": [31, 63, 127]
            }
        },
        "SVR": {
            "model": Pipeline([('scaler', MinMaxScaler()), ('regressor', SVR())]),
            "params": {
                "regressor__C": [0.1, 1.0, 10.0, 100.0], 
                "regressor__gamma": ["scale", "auto", 0.1, 0.01], 
                "regressor__kernel": ["linear", "rbf"],
                "regressor__epsilon": [0.000001, 0.0001, 0.01, 1]
            }
        },
    }

def run_pipeline_for_task(task_name, config, df_clean, freq_minutes):
    print(f"\n{'='*60}\n[Trainer {freq_minutes}m] STARTING PIPELINE FOR TASK: {task_name.upper()}\n{'='*60}")
    
    target_col = config["target"]
    features_list = config["features"]
    use_lags = config.get("use_lags", False)
    lag_target = config.get("lag_target", True) 
    
    virtual_ratio = get_virtual_ratio(freq_minutes)
    
    # Dynamic Paths based on frequency
    task_dir = os.path.join(BASE_MODEL_DIR, f"{freq_minutes}m", task_name)
    archive_dir = os.path.join(task_dir, "models_archive")
    best_dir = os.path.join(task_dir, "best_model")
    plots_dir = os.path.join(archive_dir, "plots")
    
    for d in [archive_dir, best_dir, plots_dir]: os.makedirs(d, exist_ok=True)

    extended_features_list = get_extended_features_list(features_list, use_lags)
    df_train_final_list, df_test_final_list = [], []

    for board_id in ACTIVE_BOARDS:
        df_b = df_clean[df_clean['id_board'] == board_id].copy()
        if df_b.empty: continue
        
        split_idx = int(len(df_b) * TRAIN_SPLIT_PERCENTAGE)
        df_train_b = df_b.iloc[:split_idx]
        df_test_b = df_b.iloc[split_idx:]


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

    df_train_final.dropna(subset=model_features + [target_col], inplace=True)
    df_test_final.dropna(subset=model_features + [target_col], inplace=True)

    if df_train_final.empty or df_test_final.empty:
        print(f"[{task_name}] Error: Empty datasets after processing.")
        return

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
        print(f"\n[{task_name}] Training model: {name}...")
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
            model=best_model, model_name=name, training_time=training_time, inf_time=inf_time,
            best_params=best_params, archive_dir=archive_dir, plots_dir=plots_dir
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

def main():
    print("[Trainer] Starting Multi-Frequency Global Pipeline...")
    
    for freq in DEFAULT_FREQS:
        print(f"\n=== BEGIN TRAINING FOR FREQUENCY {freq} MINUTES ===")
        df_clean = fetch_clean_data(freq)
        
        if df_clean.empty:
            print(f"[Trainer] Insufficient data for {freq}m. Please run cleaner.py first.")
            continue
        
        all_env_features = TASKS["t1"]["features"]
        env_output_dir = os.path.join(BASE_MODEL_DIR, f"{freq}m", "env_forecasters")
        train_environmental_arimas(df_clean, all_env_features, env_output_dir, freq)
        
        for task_name, config in TASKS.items():
            run_pipeline_for_task(task_name, config, df_clean, freq)
            
    print(f"\n[Trainer] Multi-Frequency Pipeline completed! Artifacts saved in {BASE_MODEL_DIR}.")

if __name__ == "__main__":
    main()