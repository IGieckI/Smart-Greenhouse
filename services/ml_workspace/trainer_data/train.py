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
import pmdarima as pm  

from sklearn.pipeline import Pipeline


from shared_core.config import *
from shared_core.tasks import TASKS

# Import condiviso
sys.path.append('/app')
# Rimosso create_virtual_datasets che non esiste più e causava ImportError
from shared_core.preprocessing import create_lagged_features 

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


# def log_and_evaluate(y_test, y_pred, features_names, model, model_name, training_time, inf_time, best_params, archive_dir, plots_dir):
#     mae = mean_absolute_error(y_test, y_pred)
#     rmse = np.sqrt(mean_squared_error(y_test, y_pred))
#     r2 = r2_score(y_test, y_pred)
    
#     importance_dict = {}
#     if hasattr(model, 'feature_importances_'):
#         importances = model.feature_importances_
#         importance_dict = {feat: float(imp) for feat, imp in zip(features_names, importances)}
#         importance_dict = dict(sorted(importance_dict.items(), key=lambda item: item[1], reverse=True))

#     report = {
#         "model_name": model_name,
#         "best_params": best_params,
#         "metrics": {"MAE": round(mae, 3), "RMSE": round(rmse, 3), "R_squared": round(r2, 3)},
#         "performance": {"training_time_seconds": round(training_time, 4), "inference_time_seconds": round(inf_time, 4)},
#         "feature_importance": importance_dict
#     }
    
#     with open(os.path.join(archive_dir, f"{model_name}_metrics.json"), "w") as f:
#         json.dump(report, f, indent=4)
        
#     plot_predictions(y_test, y_pred, model_name, mae, plots_dir)
#     return report, mae

def log_and_evaluate(y_test, y_pred, features_names, model, model_name, training_time, inf_time, best_params, archive_dir, plots_dir):
    mae = mean_absolute_error(y_test, y_pred)
    rmse = np.sqrt(mean_squared_error(y_test, y_pred))
    r2 = r2_score(y_test, y_pred)
    
    importance_dict = {}
    
    # 1. Estrai il modello finale dalla Pipeline (se presente)
    final_estimator = model.named_steps['regressor'] if hasattr(model, 'named_steps') else model

    # 2. Estrai Importanza (Alberi) o Coefficienti (Modelli Lineari)
    if hasattr(final_estimator, 'feature_importances_'):
        importances = final_estimator.feature_importances_
        importance_dict = {feat: float(imp) for feat, imp in zip(features_names, importances)}
        importance_dict = dict(sorted(importance_dict.items(), key=lambda item: item[1], reverse=True))
    elif hasattr(final_estimator, 'coef_'):
        importances = final_estimator.coef_
        importance_dict = {feat: float(imp) for feat, imp in zip(features_names, importances)}
        # Usiamo il valore assoluto per capire "l'impatto" dei coefficienti lineari
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

# ==========================================
# NEL METODO train_environmental_arimas:
# ==========================================
def train_environmental_arimas(df_clean, features, output_dir):
    print(f"\n{'='*60}\n[Trainer] ADDESTRAMENTO ARIMA AMBIENTALI INDIPENDENTI\n{'='*60}")
    os.makedirs(output_dir, exist_ok=True)
    
    df_train = df_clean[df_clean['id_board'].isin(ACTIVE_BOARDS)].copy()
    
    for feat in features:
        print(f"Addestramento per: {feat}...")
        y = df_train[feat].dropna().tail(ENV_ARIMA_TRAIN_TAIL) 
        
        best_model = pm.auto_arima(y, seasonal=False, stepwise=True, suppress_warnings=True)
        print(f"-> Ottimale per {feat}: {best_model.order}")
        
        # SALVIAMO IL MODELLO INTERO, NON SOLO L'ORDINE
        joblib.dump(best_model, os.path.join(output_dir, f"arima_{feat}.joblib"))
        
    print("Modelli ambientali salvati con successo.")

def generate_pipeline(model):
    return Pipeline([
                ('scaler', MinMaxScaler()), 
                ('regressor', model)
            ])

def run_pipeline_for_task(task_name, config, df_clean):
    print(f"\n{'='*60}\n[Trainer] AVVIO PIPELINE PER IL TASK: {task_name.upper()}\n{'='*60}")
    
    target_col = config["target"]
    features_list = config["features"]
    use_lags = config.get("use_lags", False)
    lag_target = config.get("lag_target", True) 
    
    task_dir = os.path.join(BASE_MODEL_DIR, task_name)
    archive_dir = os.path.join(task_dir, "models_archive")
    best_dir = os.path.join(task_dir, "best_model")
    plots_dir = os.path.join(archive_dir, "plots")
    
    os.makedirs(archive_dir, exist_ok=True)
    os.makedirs(best_dir, exist_ok=True)
    os.makedirs(plots_dir, exist_ok=True)

    # [Trainer] PREPARAZIONE DATI E LAG PER SINGOLA BOARD
    df_train_final_list, df_test_final_list = [], []

    for board_id in ACTIVE_BOARDS:
        df_b = df_clean[df_clean['id_board'] == board_id].copy()
        if df_b.empty: continue
        
        # 1. Split Cronologico per singola board
        split_idx = int(len(df_b) * TRAIN_SPLIT_PERCENTAGE)
        df_train_b = df_b.iloc[:split_idx]
        df_test_b = df_b.iloc[split_idx:]

        # 2. Creazione Lag ISOLATA (Nessuna contaminazione tra board)
        if use_lags:
            df_train_b = create_lagged_features(df_train_b, target_col, features_list, lags=DEFAULT_LAGS, lag_target=lag_target)
            df_test_b = create_lagged_features(df_test_b, target_col, features_list, lags=DEFAULT_LAGS, lag_target=lag_target)

        df_train_final_list.append(df_train_b)
        df_test_final_list.append(df_test_b)

    # # # 3. Solo ORA concateniamo per l'addestramento globale
    # # df_train_final = pd.concat(df_train_final_list).sort_index()
    # # df_test_final = pd.concat(df_test_final_list).sort_index()

    # # # df_train_list, df_test_list = [], []

    # # # for board_id in ACTIVE_BOARDS:
    # # #     df_b = df_clean[df_clean['id_board'] == board_id].copy()
    # # #     if df_b.empty: continue
        
    # # #     # Splittiamo cronologicamente per mantenere la sequenza temporale
    # # #     split_idx = int(len(df_b) * TRAIN_SPLIT_PERCENTAGE)
    # # #     df_train_list.append(df_b.iloc[:split_idx])
    # # #     df_test_list.append(df_b.iloc[split_idx:])

    # # # Uniamo le teste (Train) e le code (Test) delle due board
    # # # df_train_raw = pd.concat(df_train_list).sort_index()
    # # # df_test_raw = pd.concat(df_test_list).sort_index()

    # # df_train_raw = df_train_final
    # # df_test_raw = df_test_final

    # # # ==========================================
    # # # FEATURE ENGINEERING (Lags)
    # # # ==========================================
    # # if use_lags:
    # #     print(f"[{task_name}] Creazione feature ritardate (lags={DEFAULT_LAGS})...")
    # #     df_train_final = create_lagged_features(df_train_raw, target_col, features_list, lags=DEFAULT_LAGS, lag_target=lag_target)
    # #     df_test_final = create_lagged_features(df_test_raw, target_col, features_list, lags=DEFAULT_LAGS, lag_target=lag_target)
        
    # #     model_features = [col for col in df_train_final.columns if ('lag' in col and (lag_target or target_col not in col)) or col in features_list]
    # # else:
    # #     print(f"[{task_name}] Training puntuale (senza lag)...")
    # #     df_train_final = df_train_raw.copy()
    # #     df_test_final = df_test_raw.copy()

    # 3. Solo ORA concateniamo per l'addestramento globale
    df_train_final = pd.concat(df_train_final_list).sort_index()
    df_test_final = pd.concat(df_test_final_list).sort_index()

    # ==========================================
    # DEFINIZIONE FEATURE FINALI (I lag sono già calcolati!)
    # ==========================================
    if use_lags:
        # Recupera i nomi di tutte le colonne utili (feature originali + lag generati)
        model_features = [col for col in df_train_final.columns if ('lag' in col and (lag_target or target_col not in col)) or col in features_list]
    else:
        model_features = features_list 

    # Taglio dei buchi temporali (drop NaN)
    df_train_final.dropna(subset=model_features + [target_col], inplace=True)
    df_test_final.dropna(subset=model_features + [target_col], inplace=True)
    model_features = features_list 

    # Taglio dei buchi temporali (drop NaN)
    df_train_final.dropna(subset=model_features + [target_col], inplace=True)
    df_test_final.dropna(subset=model_features + [target_col], inplace=True)

    if df_train_final.empty or df_test_final.empty:
        print(f"[{task_name}] Errore: I dataset sono vuoti dopo il preprocessing.")
        return

    # X_train, y_train = df_train_final[model_features], df_train_final[target_col]
    # X_test, y_test = df_test_final[model_features], df_test_final[target_col]

    # print(f"[{task_name}] Standardizzazione dei dati...")
    # scaler = MinMaxScaler()
    # X_train_scaled = scaler.fit_transform(X_train)
    # X_test_scaled = scaler.transform(X_test)

    X_train, y_train = df_train_final[model_features], df_train_final[target_col]
    X_test, y_test = df_test_final[model_features], df_test_final[target_col]


    # Esempio di aggiornamento in models_grids (train.py)
    models_grids = {
        "Ridge": {
            "model": generate_pipeline(Ridge()),
            "params": {
                "regressor__alpha": [0.01, 0.1, 1.0, 10.0, 100.0] # Modificato il prefisso per la pipeline
            }
        },
        "RandomForest": {
            "model": generate_pipeline(RandomForestRegressor(random_state=42, n_jobs=1)), 
            "params": {
                "regressor__n_estimators": [100, 300, 500], 
                "regressor__max_depth": [10, 20, None], 
                "regressor__min_samples_split": [2, 5, 10]
            }
        },
        "LightGBM": {
            "model": generate_pipeline(LGBMRegressor(random_state=42, verbose=-1, n_jobs=1)),
            "params": {
                      "regressor__n_estimators": [100, 300, 500],
                      "regressor__learning_rate": [0.01, 0.05, 0.1],
                      "regressor__num_leaves": [31, 63, 127]}
                    },
        "SVR": {
            "model": generate_pipeline(SVR()), 
            "params": {
                "regressor__C": [0.1, 1.0, 10.0, 100.0], 
                "regressor__gamma": ["scale", "auto", 0.1, 0.01], 
                "regressor__kernel": ["linear", "rbf"],
                "regressor__epsilon": [0.000001, 0.0001, 0.01, 1]}
            },
    }

    tscv = TimeSeriesSplit(n_splits=3)
    results = {}
    best_overall_model = None
    best_overall_mae = float('inf')
    best_model_name = ""

    # for name, config in models_grids.items():
    #     print(f"\n[{task_name}] Addestramento modello: {name}...")
        
    #     if name == "AutoARIMA":
    #         start_time = time.time()
    #         best_model = pm.auto_arima(
    #             y=y_train, 
    #             X=X_train_scaled, 
    #             seasonal=False, 
    #             stepwise=True,
    #             suppress_warnings=True,
    #             error_action="ignore"
    #         )

    #         training_time = time.time() - start_time
    #         best_params = {"order": best_model.order}
            
    #         start_inf = time.time()
    #         y_pred = best_model.predict(n_periods=len(X_test_scaled), X=X_test_scaled)
    #         inf_time = time.time() - start_inf
            
    #     else:
    #         grid_search = GridSearchCV(
    #             estimator=config["model"], # È la Pipeline!
    #             param_grid=config["params"],
    #             cv=tscv,
    #             scoring='neg_mean_absolute_error',
    #             n_jobs=-1,
    #             verbose=2
    #         )
            
    #         start_time = time.time()
    #         grid_search.fit(X_train, y_train) # <- USA X_train RAW
    #         training_time = time.time() - start_time

    #         # grid_search = GridSearchCV(
    #         #     estimator=config["model"],
    #         #     param_grid=config["params"],
    #         #     cv=tscv,
    #         #     scoring='neg_mean_absolute_error',
    #         #     n_jobs=-1,
    #         #     verbose=2
    #         # )
            
    #         # start_time = time.time()
    #         # grid_search.fit(X_train_scaled, y_train)
    #         # training_time = time.time() - start_time

    #         best_model = grid_search.best_estimator_
    #         best_params = grid_search.best_params_
            
    #         start_inf = time.time()
    #         y_pred = best_model.predict(X_test_scaled)
    #         inf_time = time.time() - start_inf

    #     print(f"[{task_name} - {name}] Migliori parametri: {best_params}")

    #     report, mae = log_and_evaluate(
    #         y_test=y_test, y_pred=y_pred, features_names=model_features,
    #         model=best_model, model_name=name,
    #         training_time=training_time, inf_time=inf_time,
    #         best_params=best_params, archive_dir=archive_dir, plots_dir=plots_dir
    #     )
        
    #     results[name] = report["metrics"]
    #     joblib.dump(best_model, os.path.join(archive_dir, f"{name}.joblib"))

    #     if mae < best_overall_mae:
    #         best_overall_mae = mae
    #         best_overall_model = best_model
    #         best_model_name = name

    for name, config in models_grids.items():
        print(f"\n[{task_name}] Addestramento modello: {name}...")
        
        # ELIMINATO L'IF SU AUTOARIMA. Solo GridSearchCV per i modelli ML!
        grid_search = GridSearchCV(
            estimator=config["model"], # È la Pipeline!
            param_grid=config["params"],
            cv=tscv,
            scoring='neg_mean_absolute_error',
            n_jobs=-1,
            verbose=2
        )
        
        start_time = time.time()
        # PASSIAMO I DATI RAW. Lo scaler è dentro la pipeline!
        grid_search.fit(X_train, y_train) 
        training_time = time.time() - start_time

        best_model = grid_search.best_estimator_
        best_params = grid_search.best_params_
        
        start_inf = time.time()
        # PASSIAMO I DATI RAW. La pipeline li scala prima di predire!
        y_pred = best_model.predict(X_test) 
        inf_time = time.time() - start_inf

        print(f"[{task_name} - {name}] Migliori parametri: {best_params}")

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

    # print(f"\n[{task_name}] Il Miglior Modello è: {best_model_name} (MAE: {best_overall_mae:.3f})")

    # joblib.dump(best_overall_model, os.path.join(best_dir, "best_model.joblib"))
    # joblib.dump(scaler, os.path.join(best_dir, "scaler.joblib"))
    
    # with open(os.path.join(best_dir, "best_model_info.json"), "w") as f:
    #     json.dump({"best_model": best_model_name, "mae": best_overall_mae, "target": target_col}, f)

    # formatted_results = {name: {"MAE": res["MAE"]} for name, res in results.items()}
    # plot_models_comparison(formatted_results, plots_dir)
    print(f"\n[{task_name}] Il Miglior Modello è: {best_model_name} (MAE: {best_overall_mae:.3f})")

    # Salva il miglior modello (Pipeline)
    joblib.dump(best_overall_model, os.path.join(best_dir, "best_model.joblib"))
    # RIMOSSO joblib.dump(scaler, ...) perché non serve più!
    
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
    
    all_env_features = TASKS["t1"]["features"]
    train_environmental_arimas(df_clean, all_env_features, os.path.join(BASE_MODEL_DIR, "env_forecasters"))
    
    for task_name, config in TASKS.items():
        run_pipeline_for_task(task_name, config, df_clean)
        
    print(f"\n[Trainer] Pipeline Multi-Task completata con successo. Tutti gli artefatti sono in {BASE_MODEL_DIR}.")


if __name__ == "__main__":
    main()