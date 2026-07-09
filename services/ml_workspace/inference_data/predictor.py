import pandas as pd
import numpy as np
import sys

sys.path.append('/app')
from shared_core.config import *
from shared_core.preprocessing import build_advanced_features, get_extended_features_list, create_lagged_features


def recursive_multistep_inference(
    T_current_data: pd.DataFrame, 
    prophet_models: dict, 
    ml_model_pipeline, 
    task_config: dict, 
    freq_minutes: int
) -> list:
    
    features = task_config["features"].copy()
    if USE_INDOOR_FEATURE and 'is_indoor' not in features:
        features.append('is_indoor')
        
    use_lags = task_config.get("use_lags", False)
    lag_target = task_config.get("lag_target", True)
    task_lags = task_config.get("lags", DEFAULT_LAGS)
    
    virtual_ratio = get_virtual_ratio(freq_minutes)
    
    if use_lags:
        steps = int(180 / freq_minutes)
    else:
        steps = 1 
        
    last_time = T_current_data.index[-1]
    
    
    future_dates = [last_time + pd.Timedelta(minutes=freq_minutes * (i + 1)) for i in range(steps)]
    
    


    future_dates_naive = [t.tz_localize(None) if t.tz is not None else t for t in future_dates]
    df_prophet_future = pd.DataFrame({'ds': future_dates_naive})
    

    if USE_INDOOR_FEATURE and 'is_indoor' in T_current_data.columns:
        board_indoor_val = T_current_data['is_indoor'].iloc[-1]
        df_prophet_future['is_indoor'] = board_indoor_val
    
    env_forecasts = {}
    for feat in features:
        if feat in prophet_models:
            forecast = prophet_models[feat].predict(df_prophet_future)
            env_forecasts[feat] = forecast['yhat'].values
        elif feat == 'is_indoor':
            env_forecasts[feat] = [board_indoor_val] * steps
            
    df_future_env = pd.DataFrame(env_forecasts)
    df_future_env.index = future_dates
    
    target_predictions = []
    
    cols_to_keep = features + ['leaf_temp']
    if 'is_indoor' in T_current_data.columns and 'is_indoor' not in cols_to_keep:
        cols_to_keep.append('is_indoor')
        
    history = T_current_data[cols_to_keep].copy()



    expected_features = list(ml_model_pipeline.feature_names_in_)

    for step_i in range(steps):
        current_env_row = df_future_env.iloc[[step_i]].copy()
        
        current_env_row['leaf_temp'] = 0.0 
        history_temp = pd.concat([history, current_env_row])
        
        is_ml_step = ((step_i + 1) % virtual_ratio == 0) if use_lags else True
        
        if is_ml_step:
            history_advanced = build_advanced_features(history_temp, features, use_lags, virtual_ratio)
            
            if not use_lags:
                X_infer = history_advanced[expected_features].iloc[-1:]
            else:
                extended_features = get_extended_features_list(features, use_lags)
                history_lagged = create_lagged_features(history_advanced, 'leaf_temp', extended_features, virtual_ratio, lags=task_lags, lag_target=lag_target)
                
                X_infer = history_lagged[expected_features].iloc[-1:]
                
                if X_infer.empty:
                    raise ValueError(f"Unable to extract lags at step {step_i}. The dataset became empty.")
                    
            pred_leaf = ml_model_pipeline.predict(X_infer)[0]

            current_env_row['leaf_temp'] = pred_leaf
            
            target_predictions.append({
                "timestamp": future_dates[step_i].isoformat(),
                "value": round(float(pred_leaf), 3)
            })
            
        history = pd.concat([history, current_env_row])
        max_history_needed = (task_lags + 2) * virtual_ratio
        history = history.tail(max_history_needed)

    return [p["value"] for p in target_predictions]




def ensemble_multistep_inference(
    T_current_data: pd.DataFrame,
    prophet_models: dict,
    ml_models: dict, 
    task_configs: dict, 
    freq_minutes: int,
    mae_env: float = 0.5, 
    mae_auto: float = 0.5 
) -> dict:    
    inv_mae_env = 1.0 / (mae_env + 1e-6)
    inv_mae_auto = 1.0 / (mae_auto + 1e-6)
    total_inv = inv_mae_env + inv_mae_auto
    
    w_env = inv_mae_env / total_inv
    w_auto = inv_mae_auto / total_inv

    df_patched = T_current_data.copy()
    
    soft_cfg = task_configs["soft"]
    virtual_ratio = get_virtual_ratio(freq_minutes)
    
    soft_features = soft_cfg["features"].copy()
    if USE_INDOOR_FEATURE and 'is_indoor' not in soft_features:
        soft_features.append('is_indoor')
    
    history_adv = build_advanced_features(df_patched, soft_features, soft_cfg.get("use_lags", False), virtual_ratio)
    ext_feat_soft = get_extended_features_list(soft_features, soft_cfg.get("use_lags", False))
    
    X_soft = history_adv[ext_feat_soft].dropna()
    
    generated_history = []
    if not X_soft.empty:
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
        df_patched, prophet_models, ml_models["auto"], task_configs["auto"], freq_minutes
    )

    preds_env = recursive_multistep_inference(
        T_current_data, prophet_models, ml_models["env"], task_configs["env"], freq_minutes
    )

    blended = [round(float(w_auto * p_a + w_env * p_e), 3) for p_a, p_e in zip(preds_auto, preds_env)]

    return {
        "weights": {"autoregressive": round(w_auto, 3), "environmental": round(w_env, 3)},
        "generated_history": generated_history,
        "forecast_env": preds_env,
        "forecast_auto": preds_auto,
        "forecast_blended": blended
    }