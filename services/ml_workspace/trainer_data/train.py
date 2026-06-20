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
from sklearn.ensemble import RandomForestRegressor
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

# Import condiviso
sys.path.append('/app')
from shared_core.preprocessing import create_virtual_datasets, create_lagged_features

INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
INFLUX_ORG = os.getenv("INFLUX_ORG", "iot_org")
BUCKET_CLEAN = "sensor_data_clean"

MODEL_DIR = "/app/shared_core"
PLOTS_DIR = os.path.join(MODEL_DIR, "plots")
os.makedirs(PLOTS_DIR, exist_ok=True)

# Configurazione
TRAIN_BOARDS = ["3750866944"]
TEST_BOARDS = ["9"]
FEATURES = ['air_temp', 'humidity', 'pressure', 'water_temp', 'tds', 'soil_moisture', 'light_lux']
TARGET = 'leaf_temp'

def generate_plots(df_clean):
    """Genera e salva i grafici delle serie storiche pulite."""
    print("[Trainer] Generazione plot in corso...")
    
    # 1. Plot della Leaf Temp vs Air Temp
    plt.figure(figsize=(14, 6))
    sns.lineplot(data=df_clean, x=df_clean.index, y='leaf_temp', label='Leaf Temp', color='green')
    sns.lineplot(data=df_clean, x=df_clean.index, y='air_temp', label='Air Temp', color='orange', alpha=0.6)
    plt.title('Confronto Leaf Temp vs Air Temp (Dati Puliti)')
    plt.ylabel('Temperatura (°C)')
    plt.xlabel('Tempo')
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, "temperatures_comparison.png"))
    plt.close()

    # 2. Plot delle anomalie riparate (es. TDS)
    plt.figure(figsize=(14, 4))
    sns.lineplot(data=df_clean, x=df_clean.index, y='tds', color='blue')
    plt.title('Andamento TDS nel tempo (Senza spike)')
    plt.ylabel('TDS')
    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, "tds_cleaned.png"))
    plt.close()

def evaluate_and_log_model(model, X_test, y_test, features_names, train_time, model_name):
    """Valuta il modello e salva le metriche in JSON."""
    start_inf = time.time()
    y_pred = model.predict(X_test)
    inf_time = time.time() - start_inf
    
    mae = mean_absolute_error(y_test, y_pred)
    rmse = np.sqrt(mean_squared_error(y_test, y_pred))
    r2 = r2_score(y_test, y_pred)
    
    importance_dict = {}
    if hasattr(model, 'feature_importances_'):
        importances = model.feature_importances_
        importance_dict = {feat: float(imp) for feat, imp in zip(features_names, importances)}
        importance_dict = dict(sorted(importance_dict.items(), key=lambda item: item[1], reverse=True))

    report = {
        "model_name": model_name,
        "metrics": {"MAE_Celsius": round(mae, 3), "RMSE_Celsius": round(rmse, 3), "R_squared": round(r2, 3)},
        "performance": {"training_time_seconds": round(train_time, 4), "inference_time_seconds": round(inf_time, 4)},
        "feature_importance": importance_dict
    }
    
    with open(os.path.join(MODEL_DIR, f"{model_name}_metrics.json"), "w") as f:
        json.dump(report, f, indent=4)
    return report

def fetch_clean_data():
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    query = f'''
        from(bucket: "{BUCKET_CLEAN}")
          |> range(start: 0)
          |> filter(fn: (r) => r._measurement == "sensor_measurements")
          |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
    '''
    df = client.query_api().query_data_frame(query)
    
    # NOVITÀ: Gestione del caso in cui Influx restituisca una lista di DataFrame
    if isinstance(df, list):
        if len(df) == 0:
            return pd.DataFrame()  # Ritorna un DataFrame vuoto di sicurezza
        df = pd.concat(df, ignore_index=True)
        
    if not df.empty:
        df.set_index('_time', inplace=True)
        df.sort_index(inplace=True)
    return df

def main():
    print("[Trainer] Caricamento dati puliti...")
    df_clean = fetch_clean_data()
    
    if df_clean.empty:
        print("[Trainer] Dati insufficienti. Esegui prima cleaner.py.")
        return

    # 1. Generazione Plot
    generate_plots(df_clean)

    # 2. Suddivisione Boards
    df_board_1 = df_clean[df_clean['id_board'].isin(TRAIN_BOARDS)].copy()
    df_board_9 = df_clean[df_clean['id_board'].isin(TEST_BOARDS)].copy()

    # 3. Split Cronologico 80/20 su Board 1 (Train / Val)
    split_idx = int(len(df_board_1) * 0.8)
    df_train_raw = df_board_1.iloc[:split_idx].copy()
    df_val_raw = df_board_1.iloc[split_idx:].copy()
    df_test_raw = df_board_9.copy()

    # 4. Normalizzazione (Min-Max Scaler sulle sole X)
    print("[Trainer] Normalizzazione dei dati (MinMaxScaler)...")
    scaler = MinMaxScaler()
    # Fittiamo SOLO sul Train Set
    scaler.fit(df_train_raw[FEATURES])
    
    # Salviamo lo scaler per l'inferenza
    joblib.dump(scaler, os.path.join(MODEL_DIR, "scaler.pkl"))

    # Applichiamo la trasformazione a tutti i set
    for df in [df_train_raw, df_val_raw, df_test_raw]:
        df[FEATURES] = scaler.transform(df[FEATURES])

    print("--------------------------------------------------")
    print("[Trainer] Avvio Task 1 (V1 - Modello Istantaneo)")
    
    # Augmentation solo sul Train
    train_v1_sets = create_virtual_datasets(df_train_raw, target_freq_min=30, orig_freq_min=5)
    df_train_v1 = pd.concat(train_v1_sets).dropna(subset=FEATURES + [TARGET])
    
    # Resample per Validation e Test (senza augmentation)
    df_val_v1 = df_val_raw.resample('30min').first().dropna(subset=FEATURES + [TARGET])
    df_test_v1 = df_test_raw.resample('30min').first().dropna(subset=FEATURES + [TARGET])

    X_train_v1, y_train_v1 = df_train_v1[FEATURES], df_train_v1[TARGET]
    X_val_v1, y_val_v1 = df_val_v1[FEATURES], df_val_v1[TARGET]
    X_test_v1, y_test_v1 = df_test_v1[FEATURES], df_test_v1[TARGET]

    start_time = time.time()
    model_v1 = RandomForestRegressor(n_estimators=100, random_state=42)
    model_v1.fit(X_train_v1, y_train_v1)
    train_time_v1 = time.time() - start_time
    
    print(f"[Trainer V1] R2 su Validation (Board 1): {model_v1.score(X_val_v1, y_val_v1):.4f}")
    print(f"[Trainer V1] R2 su Test (Board 9): {model_v1.score(X_test_v1, y_test_v1):.4f}")
    
    evaluate_and_log_model(model_v1, X_test_v1, y_test_v1, FEATURES, train_time_v1, "V1_instant_model")
    joblib.dump(model_v1, os.path.join(MODEL_DIR, "V1_instant_model.pkl"))

    print("--------------------------------------------------")
    print("[Trainer] Avvio Task 2 (V2 - Modello Autoregressivo 3h)")
    
    # Creiamo i Lag per tutti i set (6 lags = 3h)
    df_train_lagged = create_lagged_features(df_train_raw, TARGET, FEATURES, lags=6)
    df_val_lagged = create_lagged_features(df_val_raw, TARGET, FEATURES, lags=6)
    df_test_lagged = create_lagged_features(df_test_raw, TARGET, FEATURES, lags=6)

    # Definiamo le nuove features ritardate
    lagged_features = [col for col in df_train_lagged.columns if 'lag' in col or col in FEATURES]

    # Augmentation sul Train Lagged
    train_v2_sets = create_virtual_datasets(df_train_lagged, target_freq_min=30, orig_freq_min=5)
    df_train_v2 = pd.concat(train_v2_sets).dropna(subset=lagged_features + [TARGET])
    
    df_val_v2 = df_val_lagged.resample('30min').first().dropna(subset=lagged_features + [TARGET])
    df_test_v2 = df_test_lagged.resample('30min').first().dropna(subset=lagged_features + [TARGET])

    X_train_v2, y_train_v2 = df_train_v2[lagged_features], df_train_v2[TARGET]
    X_val_v2, y_val_v2 = df_val_v2[lagged_features], df_val_v2[TARGET]
    X_test_v2, y_test_v2 = df_test_v2[lagged_features], df_test_v2[TARGET]

    start_time = time.time()
    model_v2 = RandomForestRegressor(n_estimators=100, random_state=42)
    model_v2.fit(X_train_v2, y_train_v2)
    train_time_v2 = time.time() - start_time

    print(f"[Trainer V2] R2 su Validation (Board 1): {model_v2.score(X_val_v2, y_val_v2):.4f}")
    print(f"[Trainer V2] R2 su Test (Board 9): {model_v2.score(X_test_v2, y_test_v2):.4f}")

    evaluate_and_log_model(model_v2, X_test_v2, y_test_v2, lagged_features, train_time_v2, "V2_autoregressive_model")
    joblib.dump(model_v2, os.path.join(MODEL_DIR, "V2_autoregressive_model.pkl"))
    
    print("[Trainer] Pipeline completata con successo!")

if __name__ == "__main__":
    main()