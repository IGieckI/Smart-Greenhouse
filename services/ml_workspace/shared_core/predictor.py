import pandas as pd
import numpy as np

from shared_core.config import *
from shared_core.preprocessing import build_advanced_features, get_extended_features_list, create_lagged_features

def recursive_multistep_inference(
    T_current_data: pd.DataFrame, 
    arima_models: dict, 
    ml_model_pipeline, 
    task_config: dict, 
    freq_minutes: int
) -> list:
    
    features = task_config["features"]
    use_lags = task_config["use_lags"]
    lag_target = task_config["lag_target"]
    
    # Calcolo dinamico
    virtual_ratio = get_virtual_ratio(freq_minutes)
    horizon_minutes = task_config.get("horizon_minutes", 0)
    steps = max(1, horizon_minutes // freq_minutes) if horizon_minutes > 0 else 1
    
    # 1. Previsione Variabili Ambientali Continue
    env_forecasts = {}
    for feat in features:
        env_forecasts[feat] = arima_models[feat].predict(n_periods=steps)
    
    df_future_env = pd.DataFrame(env_forecasts)
    last_time = T_current_data.index[-1]
    
    # Le date future dipendono dalla frequenza passata in input!
    future_dates = [last_time + pd.Timedelta(minutes=freq_minutes * (i + 1)) for i in range(steps)]
    df_future_env.index = future_dates
    
    target_predictions = []
    history = T_current_data.copy()

    # 2. Ciclo Ricorsivo
    for step_i in range(steps):
        current_env_row = df_future_env.iloc[[step_i]].copy()
        current_env_row['leaf_temp'] = np.nan 
        history_temp = pd.concat([history, current_env_row])
        
        is_ml_step = ((step_i + 1) % virtual_ratio == 0) if use_lags else True
        
        if is_ml_step:
            history_advanced = build_advanced_features(history_temp, features, use_lags, virtual_ratio)
            extended_features = get_extended_features_list(features, use_lags)
            
            if not use_lags:
                X_infer = history_advanced[extended_features].iloc[-1:]
                pred_leaf = ml_model_pipeline.predict(X_infer)[0]
            else:
                history_lagged = create_lagged_features(history_advanced, 'leaf_temp', extended_features, virtual_ratio, lags=DEFAULT_LAGS, lag_target=lag_target)
                model_features = [col for col in history_lagged.columns if ('lag' in col and (lag_target or 'leaf_temp' not in col)) or col in extended_features]
                X_infer = history_lagged[model_features].iloc[-1:]
                pred_leaf = ml_model_pipeline.predict(X_infer)[0]

            current_env_row['leaf_temp'] = pred_leaf
            
            target_predictions.append({
                "timestamp": future_dates[step_i].isoformat(),
                "value": round(float(pred_leaf), 3)
            })
            
        history = pd.concat([history, current_env_row])
        max_history_needed = (DEFAULT_LAGS + 2) * virtual_ratio
        history = history.tail(max_history_needed)

    return [p["value"] for p in target_predictions]