import pandas as pd
import numpy as np

from shared_core.config import *
from shared_core.preprocessing import build_advanced_features, get_extended_features_list, create_lagged_features

def recursive_multistep_inference(
    T_current_data: pd.DataFrame, 
    arima_models: dict, 
    ml_model_pipeline, 
    task_config: dict, 
    steps: int = 30
) -> list:
    
    features = task_config["features"]
    use_lags = task_config["use_lags"]
    lag_target = task_config["lag_target"]
    
    # 1. Previsione Variabili Ambientali Continue (Tutti gli N step)
    env_forecasts = {}
    for feat in features:
        env_forecasts[feat] = arima_models[feat].predict(n_periods=steps)
    
    df_future_env = pd.DataFrame(env_forecasts)
    
    last_time = T_current_data.index[-1]
    future_dates = [last_time + pd.Timedelta(minutes=NOMINAL_FREQ_MINUTES * (i + 1)) for i in range(steps)]
    df_future_env.index = future_dates
    
    target_predictions = []
    history = T_current_data.copy()

    # 2. Ciclo Ricorsivo con Accensione Selettiva
    for step_i in range(steps):
        current_env_row = df_future_env.iloc[[step_i]].copy()
        current_env_row['leaf_temp'] = np.nan 
        
        history_temp = pd.concat([history, current_env_row])
        
        # LOGICA DI ACCENSIONE: Il modello ML entra in azione solo ogni VIRTUAL_RATIO step (es. ogni 30 min)
        # Se il modello non usa lag (es. t1 stima puntuale), interviene subito.
        is_ml_step = ((step_i + 1) % VIRTUAL_RATIO == 0) if use_lags else True
        
        if is_ml_step:
            history_advanced = build_advanced_features(history_temp, features, use_lags)
            extended_features = get_extended_features_list(features, use_lags)
            
            if not use_lags:
                X_infer = history_advanced[extended_features].iloc[-1:]
                pred_leaf = ml_model_pipeline.predict(X_infer)[0]
            else:
                # I lag generati qui guardano al passato con lo span corretto grazie a VIRTUAL_RATIO
                history_lagged = create_lagged_features(history_advanced, 'leaf_temp', extended_features, lags=DEFAULT_LAGS, lag_target=lag_target)
                model_features = [col for col in history_lagged.columns if ('lag' in col and (lag_target or 'leaf_temp' not in col)) or col in extended_features]
                X_infer = history_lagged[model_features].iloc[-1:]
                pred_leaf = ml_model_pipeline.predict(X_infer)[0]

            # Fissiamo il valore predetto
            current_env_row['leaf_temp'] = pred_leaf
            
            target_predictions.append({
                "timestamp": future_dates[step_i].isoformat(),
                "value": round(float(pred_leaf), 3)
            })
            
        # 3. Aggiorniamo la history (i record senza ML_step conterranno NaN per leaf_temp, ma è voluto!)
        history = pd.concat([history, current_env_row])
        
        # Manteniamo la coda lunga abbastanza per servire il massimo lag temporale richiesto
        max_history_needed = (DEFAULT_LAGS + 2) * VIRTUAL_RATIO
        history = history.tail(max_history_needed)

    # Restituiamo SOLO i target generati (es. 6 predizioni per un orizzonte di 3 ore)
    return [p["value"] for p in target_predictions]