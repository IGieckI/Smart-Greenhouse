import pandas as pd
import numpy as np
import sys

sys.path.append('/app')
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
    
    virtual_ratio = get_virtual_ratio(freq_minutes)
    horizon_minutes = task_config.get("horizon_minutes", 0)
    steps = max(1, horizon_minutes // freq_minutes) if horizon_minutes > 0 else 1
    
    env_forecasts = {}
    for feat in features:
        env_forecasts[feat] = arima_models[feat].predict(n_periods=steps)
    
    df_future_env = pd.DataFrame(env_forecasts)
    last_time = T_current_data.index[-1]
    
    future_dates = [last_time + pd.Timedelta(minutes=freq_minutes * (i + 1)) for i in range(steps)]
    df_future_env.index = future_dates
    
    target_predictions = []
    
    # 1. Filtriamo i dati isolando solo quelli pertinenti al task
    cols_to_keep = features + ['leaf_temp']
    history = T_current_data[cols_to_keep].copy()

    # ==========================================
    # FIX: RICAVIAMO LA FIRMA ESATTA DEL MODELLO
    # Estraiamo l'ordine millimetrico delle colonne salvato 
    # nel modello durante l'addestramento.
    # ==========================================
    expected_features = list(ml_model_pipeline.feature_names_in_)

    for step_i in range(steps):
        current_env_row = df_future_env.iloc[[step_i]].copy()
        
        current_env_row['leaf_temp'] = 0.0 
        history_temp = pd.concat([history, current_env_row])
        
        is_ml_step = ((step_i + 1) % virtual_ratio == 0) if use_lags else True
        
        if is_ml_step:
            history_advanced = build_advanced_features(history_temp, features, use_lags, virtual_ratio)
            
            if not use_lags:
                # Applichiamo il filtro 'expected_features' per garantire il match perfetto
                X_infer = history_advanced[expected_features].iloc[-1:]
            else:
                extended_features = get_extended_features_list(features, use_lags)
                history_lagged = create_lagged_features(history_advanced, 'leaf_temp', extended_features, virtual_ratio, lags=DEFAULT_LAGS, lag_target=lag_target)
                
                # Applichiamo il filtro 'expected_features' anche ai lag
                X_infer = history_lagged[expected_features].iloc[-1:]
                
                if X_infer.empty:
                    raise ValueError(f"Impossibile estrarre i lag al passo {step_i}. Il dataset si è svuotato.")
                    
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


def ensemble_multistep_inference(
    T_current_data: pd.DataFrame,
    arima_models: dict,
    ml_models: dict, 
    task_configs: dict, 
    freq_minutes: int,
    soft_mae: float,
    mae_min: float = 0.3,
    mae_max: float = 1.0
) -> dict:
    
    w_auto = max(0.0, min(1.0, (mae_max - soft_mae) / (mae_max - mae_min)))
    w_env = 1.0 - w_auto

    df_patched = T_current_data.copy()
    df_patched['leaf_temp'] = np.nan 
    
    soft_cfg = task_configs["soft"]
    virtual_ratio = get_virtual_ratio(freq_minutes)
    
    history_adv = build_advanced_features(df_patched, soft_cfg["features"], soft_cfg["use_lags"], virtual_ratio)
    ext_feat_soft = get_extended_features_list(soft_cfg["features"], soft_cfg["use_lags"])
    
    X_soft = history_adv[ext_feat_soft].dropna()
    
    generated_history = []
    if not X_soft.empty:
        # Anche qui per sicurezza, estraiamo l'ordine esatto per il soft sensor
        soft_expected_features = list(ml_models["soft"].feature_names_in_)
        X_soft = X_soft[soft_expected_features]
        
        generated_leaf = ml_models["soft"].predict(X_soft)
        df_patched.loc[X_soft.index, 'leaf_temp'] = generated_leaf
        
        generated_history = [
            {"timestamp": ts.isoformat(), "value": round(float(val), 3)} 
            for ts, val in zip(X_soft.index, generated_leaf)
        ]

    df_patched['leaf_temp'] = df_patched['leaf_temp'].ffill().bfill()

    preds_auto = recursive_multistep_inference(
        df_patched, arima_models, ml_models["auto"], task_configs["auto"], freq_minutes
    )

    preds_env = recursive_multistep_inference(
        T_current_data, arima_models, ml_models["env"], task_configs["env"], freq_minutes
    )

    blended = [round(float(w_auto * p_a + w_env * p_e), 3) for p_a, p_e in zip(preds_auto, preds_env)]

    return {
        "weights": {"autoregressive": round(w_auto, 3), "environmental": round(w_env, 3)},
        "generated_history": generated_history,
        "forecast_env": preds_env,
        "forecast_auto": preds_auto,
        "forecast_blended": blended
    }