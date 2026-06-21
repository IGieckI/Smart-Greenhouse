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

# Import dei Modelli
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.ensemble import RandomForestRegressor
from sklearn.svm import SVR
from lightgbm import LGBMRegressor
from catboost import CatBoostRegressor
import pmdarima as pm  # Per AutoARIMA (SARIMAX)

# Import condiviso
sys.path.append('/app')
from shared_core.preprocessing import create_virtual_datasets, create_lagged_features

INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
INFLUX_ORG = os.getenv("INFLUX_ORG", "iot_org")
BUCKET_CLEAN = "sensor_data_clean"

# Configurazione Base Cartelle
BASE_MODEL_DIR = "/app/shared_core/models"

# Configurazione Dati
TRAIN_BOARDS = ["3750866944"]
TEST_BOARDS = ["9"]

# Definizione Dinamica dei Task (Personalizza il target e le features del v2 come preferisci)
from shared_core.tasks import TASKS

def fetch_clean_data():
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    query = f'''
        from(bucket: "{BUCKET_CLEAN}")
          |> range(start: 0)
          |> filter(fn: (r) => r._measurement == "sensor_measurements")
          |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
    '''
    df = client.query_api().query_data_frame(query)
    if isinstance(df, list):
        if len(df) == 0:
            return pd.DataFrame()
        df = pd.concat(df, ignore_index=True)
        
    if not df.empty:
        df.set_index('_time', inplace=True)
        df.sort_index(inplace=True)
    return df


def plot_predictions(y_test, y_pred, model_name, mae, plots_dir):
    """Genera un grafico Valori Reali vs Predetti per il modello e lo salva."""
    plt.figure(figsize=(12, 5))
    plt.plot(y_test.values, label='Valori Reali', color='green', alpha=0.7)
    plt.plot(y_pred, label=f'Predizioni {model_name}', color='orange', alpha=0.8, linestyle='--')
    plt.title(f'Performance {model_name} (MAE: {mae:.3f})')
    plt.xlabel('Campioni Temporali (Test Set)')
    plt.ylabel('Target Value')
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(plots_dir, f"{model_name}_predictions.png"))
    plt.close()


def plot_models_comparison(results_dict, plots_dir):
    """Genera un grafico a barre comparativo basato sul MAE di tutti i modelli."""
    names = list(results_dict.keys())
    maes = [res['MAE'] for res in results_dict.values()]
    
    plt.figure(figsize=(10, 6))
    bars = plt.barh(names, maes, color='skyblue')
    plt.xlabel('Mean Absolute Error (MAE) [Più basso è meglio]')
    plt.title('Confronto Finale Modelli')
    
    for bar in bars:
        plt.text(bar.get_width() + 0.05, bar.get_y() + bar.get_height()/2, 
                 f'{bar.get_width():.3f}', va='center')
                 
    plt.tight_layout()
    plt.savefig(os.path.join(plots_dir, "final_models_comparison.png"))
    plt.close()


def log_and_evaluate(y_test, y_pred, features_names, model, model_name, training_time, inf_time, best_params, archive_dir, plots_dir):
    """Calcola le metriche, salva il JSON e genera il grafico per un singolo modello."""
    mae = mean_absolute_error(y_test, y_pred)
    rmse = np.sqrt(mean_squared_error(y_test, y_pred))
    r2 = r2_score(y_test, y_pred)
    
    # Feature Importance (se supportata, ignorata da SVR e ARIMA)
    importance_dict = {}
    if hasattr(model, 'feature_importances_'):
        importances = model.feature_importances_
        importance_dict = {feat: float(imp) for feat, imp in zip(features_names, importances)}
        importance_dict = dict(sorted(importance_dict.items(), key=lambda item: item[1], reverse=True))

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


def train_environmental_arimas(df_clean, features, output_dir):
    """Calcola e salva il miglior ordine (p,d,q) per ogni feature ambientale."""
    print(f"\n{'='*60}\n[Trainer] ADDESTRAMENTO ARIMA AMBIENTALI INDIPENDENTI\n{'='*60}")
    os.makedirs(output_dir, exist_ok=True)
    
    df_train = df_clean[df_clean['id_board'].isin(TRAIN_BOARDS)].copy()
    env_orders = {}
    
    for feat in features:
        print(f"Ricerca ordine ottimale (p,d,q) per: {feat}...")
        # Addestriamo su un campione rappresentativo (es. ultime 2 settimane) per non far esplodere i tempi
        y = df_train[feat].dropna().tail(3360) # ~2 settimane a 6 min
        
        best_model = pm.auto_arima(y, seasonal=False, stepwise=True, suppress_warnings=True)
        env_orders[feat] = best_model.order
        print(f"-> Ottimale per {feat}: {best_model.order}")
        
    with open(os.path.join(output_dir, "env_arima_orders.json"), "w") as f:
        json.dump(env_orders, f, indent=4)
    print("Modelli ambientali salvati con successo.")



def run_pipeline_for_task(task_name, config, df_clean):
    print(f"\n{'='*60}\n[Trainer] AVVIO PIPELINE PER IL TASK: {task_name.upper()}\n{'='*60}")
    
    target_col = config["target"]
    features_list = config["features"]
    use_lags = config.get("use_lags", False)
    lag_target = config.get("lag_target", True) # Legge il flag specifico per t2/t3
    
    # Setup Cartelle Dinamiche per il task
    task_dir = os.path.join(BASE_MODEL_DIR, task_name)
    archive_dir = os.path.join(task_dir, "models_archive")
    best_dir = os.path.join(task_dir, "best_model")
    plots_dir = os.path.join(archive_dir, "plots")
    
    os.makedirs(archive_dir, exist_ok=True)
    os.makedirs(best_dir, exist_ok=True)
    os.makedirs(plots_dir, exist_ok=True)

    # Verifica presenza del target nel dataset
    if target_col not in df_clean.columns:
        print(f"[Trainer] Errore: Il target '{target_col}' non è presente nei dati per {task_name}.")
        return

    # Preparazione Dati per il Task specifico
    df_board_1 = df_clean[df_clean['id_board'].isin(TRAIN_BOARDS)].copy()
    df_test_raw = df_clean[df_clean['id_board'].isin(TEST_BOARDS)].copy()

    # DIFFERENZIAZIONE LOGICA t1 vs t3
    # if use_lags:
    #     print(f"[{task_name}] Creazione feature ritardate (lags=6, target_lagged={lag_target})...")
    #     df_train_model = create_lagged_features(df_board_1, target_col, features_list, lags=6, lag_target=lag_target)
    #     df_test_model = create_lagged_features(df_test_raw, target_col, features_list, lags=6, lag_target=lag_target)
        
    #     train_sets = create_virtual_datasets(df_train_model, target_freq_min=30, orig_freq_min=6)
    #     df_train_final = pd.concat(train_sets, ignore_index=True)
        
    #     # Le features del modello cambiano dinamicamente se escludiamo il target
    #     model_features = [col for col in df_train_model.columns if ('lag' in col and (lag_target or target_col not in col)) or col in features_list]
    if use_lags:
        print(f"[{task_name}] Creazione feature ritardate (lags=6, target_lagged={lag_target})...")
        df_train_final = create_lagged_features(df_board_1, target_col, features_list, lags=6, lag_target=lag_target)
        df_test_model = create_lagged_features(df_test_raw, target_col, features_list, lags=6, lag_target=lag_target)
        
        # NESSUN RESAMPLE! Usiamo i dati direttamente a 6 minuti.
        model_features = [col for col in df_train_final.columns if ('lag' in col and (lag_target or target_col not in col)) or col in features_list]
    else:
        print(f"[{task_name}] Training puntuale (senza lag)...")
        df_train_final = df_board_1.copy()
        df_test_model = df_test_raw.copy()
        model_features = features_list  # Usa solo le feature dirette

    # # print(f"[{task_name}] Creazione feature ritardate (lags=6) per target {target_col}...")
    # # df_train_lagged = create_lagged_features(df_board_1, target_col, features_list, lags=6)
    # # df_test_lagged = create_lagged_features(df_test_raw, target_col, features_list, lags=6)

    # # Augmentation
    # train_sets = create_virtual_datasets(df_train_lagged, target_freq_min=30, orig_freq_min=5)
    # lagged_features = [col for col in df_train_lagged.columns if 'lag' in col or col in features_list]
    
    # df_train_final = pd.concat(train_sets, ignore_index=True)
    # df_train_final.dropna(inplace=True)
    # df_test_lagged.dropna(inplace=True)

    # if df_train_final.empty or df_test_lagged.empty:
    #     print(f"[{task_name}] Errore: I dataset sono vuoti dopo il preprocessing.")
    #     return

    # X_train, y_train = df_train_final[lagged_features], df_train_final[target_col]
    # X_test, y_test = df_test_lagged[lagged_features], df_test_lagged[target_col]
    df_train_final.dropna(subset=model_features + [target_col], inplace=True)
    df_test_model.dropna(subset=model_features + [target_col], inplace=True)

    if df_train_final.empty or df_test_model.empty:
        print(f"[{task_name}] Errore: I dataset sono vuoti dopo il preprocessing.")
        return

    X_train, y_train = df_train_final[model_features], df_train_final[target_col]
    X_test, y_test = df_test_model[model_features], df_test_model[target_col]

    print(f"[{task_name}] Standardizzazione dei dati...")
    scaler = MinMaxScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    models_grids = {
        "Ridge": {"model": Ridge(), 
                  "params": {
                      "alpha": [0.01, 0.1, 1.0, 10.0, 100.0]}
                    },
        "RandomForest": {"model": RandomForestRegressor(random_state=42, n_jobs=1), 
                  "params": {
                      "n_estimators": [100, 300, 500], 
                      "max_depth": [10, 20, None], 
                      "min_samples_split": [2, 5, 10]}
                    },
        "LightGBM": {"model": LGBMRegressor(random_state=42, verbose=-1, n_jobs=1), 
                  "params": {
                      "n_estimators": [100, 300, 500],
                      "learning_rate": [0.01, 0.05, 0.1],
                      "num_leaves": [31, 63, 127]}
                    },
        "CatBoost": {"model": CatBoostRegressor(random_state=42, verbose=0, thread_count=1),
                  "params": {
                      "iterations": [200, 500, 1000], 
                      "depth": [4, 6, 8], 
                      "learning_rate": [0.01, 0.05, 0.1]}
                    },
        "SVR": {"model": SVR(), 
                  "params": {
                      "C": [0.1, 1.0, 10.0, 100.0], 
                      "gamma": ["scale", "auto", 0.1, 0.01], 
                      "kernel": ["linear", "rbf"],
                      "epsilon": [0.000001, 0.0001, 0.01, 1]}
                    },
        # "AutoARIMA": None
    }

    tscv = TimeSeriesSplit(n_splits=3)
    results = {}
    best_overall_model = None
    best_overall_mae = float('inf')
    best_model_name = ""

    for name, config in models_grids.items():
        print(f"\n[{task_name}] Addestramento modello: {name}...")
        
        if name == "AutoARIMA":
            # AutoARIMA non usa GridSearchCV
            start_time = time.time()
            best_model = pm.auto_arima(
                y=y_train, 
                X=X_train_scaled, 
                seasonal=False, # Imposta a True se identifichi chiara stagionalità
                stepwise=True,
                suppress_warnings=True,
                error_action="ignore"
            )
            training_time = time.time() - start_time
            best_params = {"order": best_model.order}
            
            start_inf = time.time()
            y_pred = best_model.predict(n_periods=len(X_test_scaled), X=X_test_scaled)
            inf_time = time.time() - start_inf
            
        else:
            grid_search = GridSearchCV(
                estimator=config["model"],
                param_grid=config["params"],
                cv=tscv,
                scoring='neg_mean_absolute_error',
                n_jobs=-1,
                verbose=2  # <--- AGGIUNGI QUESTO
            )
            
            start_time = time.time()
            grid_search.fit(X_train_scaled, y_train)
            training_time = time.time() - start_time

            best_model = grid_search.best_estimator_
            best_params = grid_search.best_params_
            
            start_inf = time.time()
            y_pred = best_model.predict(X_test_scaled)
            inf_time = time.time() - start_inf

        print(f"[{task_name} - {name}] Migliori parametri: {best_params}")

        # Logging, Plotting e Valutazione unificati
        report, mae = log_and_evaluate(
            y_test=y_test, y_pred=y_pred, features_names=model_features,
            model=best_model, model_name=name,
            training_time=training_time, inf_time=inf_time,
            best_params=best_params, archive_dir=archive_dir, plots_dir=plots_dir
        )
        
        results[name] = report["metrics"]
        joblib.dump(best_model, os.path.join(archive_dir, f"{name}.joblib"))

        if mae < best_overall_mae:
            best_overall_mae = mae
            best_overall_model = best_model
            best_model_name = name

    # Salvataggi Finali del Task
    print(f"\n[{task_name}] Il Miglior Modello è: {best_model_name} (MAE: {best_overall_mae:.3f})")

    joblib.dump(best_overall_model, os.path.join(best_dir, "best_model.joblib"))
    joblib.dump(scaler, os.path.join(best_dir, "scaler.joblib"))
    
    with open(os.path.join(best_dir, "best_model_info.json"), "w") as f:
        json.dump({"best_model": best_model_name, "mae": best_overall_mae, "target": target_col}, f)

    formatted_results = {name: {"MAE": res["MAE"]} for name, res in results.items()}
    plot_models_comparison(formatted_results, plots_dir)


def main():
    print("[Trainer] Avvio Global Pipeline Multi-Task...")
    df_clean = fetch_clean_data()
    
    if df_clean.empty:
        print("[Trainer] Dati insufficienti. Esegui prima cleaner.py.")
        return
    
    # 1. Addestra prima i modelli ambientali generali (indipendenti dal task ML)
    all_env_features = TASKS["t1"]["features"]
    train_environmental_arimas(df_clean, all_env_features, os.path.join(BASE_MODEL_DIR, "env_forecasters"))
    
    # 2. Ciclo sui Task ML specifici (t1, t2, t3)
    for task_name, config in TASKS.items():
        run_pipeline_for_task(task_name, config, df_clean)
        
    print(f"\n[Trainer] Pipeline Multi-Task completata con successo. Tutti gli artefatti sono in {BASE_MODEL_DIR}.")


if __name__ == "__main__":
    main()