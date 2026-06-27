# import pandas as pd
# import numpy as np

# from shared_core.config import *
# from shared_core.preprocessing import build_advanced_features, get_extended_features_list, create_lagged_features

# def recursive_multistep_inference(
#     T_current_data: pd.DataFrame, 
#     arima_models: dict, 
#     ml_model_pipeline, 
#     task_config: dict, 
#     steps: int = 30
# ) -> list:
    
#     features = task_config["features"]
#     use_lags = task_config["use_lags"]
#     lag_target = task_config["lag_target"]
    
#     # 1. Previsione Variabili Ambientali con ARIMA
#     env_forecasts = {}
#     for feat in features:
#         env_forecasts[feat] = arima_models[feat].predict(n_periods=steps)
    
#     df_future_env = pd.DataFrame(env_forecasts)
    
#     # 2. Generazione indici temporali futuri
#     last_time = T_current_data.index[-1]
#     future_dates = [last_time + pd.Timedelta(minutes=NOMINAL_FREQ_MINUTES * (i + 1)) for i in range(steps)]
#     df_future_env.index = future_dates
    
#     all_raw_predictions = []
#     history = T_current_data.copy()

#     # 3. Ciclo Ricorsivo
#     for step_i in range(steps):
#         current_env_row = df_future_env.iloc[[step_i]].copy()
#         current_env_row['leaf_temp'] = np.nan 
        
#         history_temp = pd.concat([history, current_env_row])
        
#         history_advanced = build_advanced_features(history_temp, features, use_lags)
#         extended_features = get_extended_features_list(features, use_lags)
        
#         if not use_lags:
#             X_infer = history_advanced[extended_features].iloc[-1:]
#             pred_leaf = ml_model_pipeline.predict(X_infer)[0]
#         else:
#             history_lagged = create_lagged_features(history_advanced, 'leaf_temp', extended_features, lags=DEFAULT_LAGS, lag_target=lag_target)
#             model_features = [col for col in history_lagged.columns if ('lag' in col and (lag_target or 'leaf_temp' not in col)) or col in extended_features]
#             X_infer = history_lagged[model_features].iloc[-1:]
#             pred_leaf = ml_model_pipeline.predict(X_infer)[0]

#         current_env_row['leaf_temp'] = pred_leaf
#         history = pd.concat([history, current_env_row])
#         history = history.tail(DEFAULT_LAGS + 2)

#         all_raw_predictions.append({
#             "timestamp": future_dates[step_i].isoformat(),
#             "value": round(float(pred_leaf), 3)
#         })

#     return [p["value"] for p in all_raw_predictions]



# # import pandas as pd
# # import numpy as np

# # from config import *


# # import pandas as pd
# # import numpy as np

# # from shared_core.config import *
# # # IMPORTANTISSIMO: Dobbiamo importare dal preprocessing le funzioni avanzate!
# # from shared_core.preprocessing import build_advanced_features, get_extended_features_list, create_lagged_features

# # def recursive_multistep_inference(
# #     T_current_data: pd.DataFrame, 
# #     arima_models: dict, 
# #     ml_model_pipeline, 
# #     task_config: dict, 
# #     steps: int = 30
# # ) -> list:
    
# #     features = task_config["features"]
# #     use_lags = task_config["use_lags"]
# #     lag_target = task_config["lag_target"]
    
# #     # 1. Previsione Variabili Ambientali con ARIMA
# #     env_forecasts = {}
# #     for feat in features:
# #         env_forecasts[feat] = arima_models[feat].predict(n_periods=steps)
    
# #     df_future_env = pd.DataFrame(env_forecasts)
    
# #     # 2. Generazione degli indici temporali futuri (FONDAMENTALE per il tempo ciclico)
# #     last_time = T_current_data.index[-1]
# #     future_dates = [last_time + pd.Timedelta(minutes=NOMINAL_FREQ_MINUTES * (i + 1)) for i in range(steps)]
# #     df_future_env.index = future_dates
    
# #     all_raw_predictions = []
# #     history = T_current_data.copy()

# #     # 3. Ciclo Ricorsivo
# #     for step_i in range(steps):
# #         current_env_row = df_future_env.iloc[[step_i]].copy()
# #         current_env_row['leaf_temp'] = np.nan 
        
# #         history_temp = pd.concat([history, current_env_row])
        
# #         # Le nuove feature avanzate vengono applicate qui, a tutto il blocco storico+corrente
# #         history_advanced = build_advanced_features(history_temp, features, use_lags)
# #         extended_features = get_extended_features_list(features, use_lags)
        
# #         if not use_lags:
# #             X_infer = history_advanced[extended_features].iloc[-1:]
# #             pred_leaf = ml_model_pipeline.predict(X_infer)[0]
# #         else:
# #             history_lagged = create_lagged_features(history_advanced, 'leaf_temp', extended_features, lags=DEFAULT_LAGS, lag_target=lag_target)
# #             model_features = [col for col in history_lagged.columns if ('lag' in col and (lag_target or 'leaf_temp' not in col)) or col in extended_features]
# #             X_infer = history_lagged[model_features].iloc[-1:]
# #             pred_leaf = ml_model_pipeline.predict(X_infer)[0]

# #         current_env_row['leaf_temp'] = pred_leaf
# #         history = pd.concat([history, current_env_row])
# #         history = history.tail(DEFAULT_LAGS + 2)

# #         # Salviamo la previsione con il suo timestamp (utile per il filtro successivo)
# #         all_raw_predictions.append({
# #             "timestamp": future_dates[step_i].isoformat(),
# #             "value": round(float(pred_leaf), 3)
# #         })

# #     # ==========================================
# #     # PREDISPOSIZIONE PER I PUNTI 3 E 4 (Sottocampionamento a 30 min)
# #     # ==========================================
# #     # Quando vorremo che l'API restituisca SOLO i dati ogni 30 minuti, 
# #     # de-commenteremo il blocco sottostante:
    
# #     # step_interval = int(TARGET_FREQ_MINUTES / NOMINAL_FREQ_MINUTES)
# #     # target_predictions = []
# #     # for i in range(step_interval - 1, len(all_raw_predictions), step_interval):
# #     #     target_predictions.append(all_raw_predictions[i])
# #     # return target_predictions

# #     # Per ora, restituiamo tutti i valori puntuali
# #     return [p["value"] for p in all_raw_predictions]


# # # def recursive_multistep_inference(
# # #     T_current_data: pd.DataFrame, 
# # #     arima_models: dict, 
# # #     ml_model_pipeline, 
# # #     task_config: dict, 
# # #     steps: int = 30
# # # ) -> list:
# # #     """
# # #     Esegue il forecasting a N step nel futuro.
# # #     T_current_data: DataFrame con l'ultimo stato noto (almeno DEFAULT_LAGS righe).
# # #     arima_models: Dizionario con i modelli pm.auto_arima già fittati per l'ambiente.
# # #     """
# # #     features = task_config["features"]
# # #     use_lags = task_config["use_lags"]
# # #     lag_target = task_config["lag_target"]
    
# # #     # 1. Previsione Variabili Ambientali con ARIMA (Srotoliamo il futuro)
# # #     env_forecasts = {}
# # #     for feat in features:
# # #         # ARIMA predice i prossimi 'steps' campioni (es. 30 step)
# # #         env_forecasts[feat] = arima_models[feat].predict(n_periods=steps)
    
# # #     df_future_env = pd.DataFrame(env_forecasts)
    
# # #     # Lista per salvare le previsioni della leaf_temp
# # #     leaf_temp_predictions = []
    
# # #     # Copia di lavoro dello storico per poter fare lo "shift" iterativo
# # #     # Deve contenere gli ultimi DEFAULT_LAGS campioni reali
# # #     history = T_current_data.copy()

# # #     # 2. Ciclo Ricorsivo Step-by-Step
# # #     for step_i in range(steps):
# # #         # Riga ambientale prevista per lo step corrente
# # #         current_env_row = df_future_env.iloc[[step_i]].copy()
        
# # #         if not use_lags:
# # #             # ==========================================
# # #             # TASK t1 / t4: Stima Puntuale (Direct Mapping)
# # #             # ==========================================
# # #             # Usiamo semplicemente le predizioni ambientali dell'ARIMA 
# # #             # e le passiamo al modello ML per ottenere la leaf_temp.
# # #             pred_leaf = ml_model_pipeline.predict(current_env_row)[0]
            
# # #         else:
# # #             # ==========================================
# # #             # TASK t2/t5 (Lag Env) e t3/t6 (Lag Env + Lag Target)
# # #             # ==========================================
# # #             # Costruiamo le feature ritardate dinamicamente
# # #             features_row = {}
            
# # #             # Aggiungiamo i valori ambientali attuali (predetti da ARIMA)
# # #             for feat in features:
# # #                 features_row[feat] = current_env_row[feat].values[0]
                
# # #                 # Aggiungiamo i lag ambientali
# # #                 for l in range(1, DEFAULT_LAGS + 1):
# # #                     features_row[f'{feat}_lag_{l}'] = history[feat].iloc[-l]
            
# # #             # Se il task è autoregressivo (t3, t6), aggiungiamo i lag della leaf_temp
# # #             if lag_target:
# # #                 for l in range(1, DEFAULT_LAGS + 1):
# # #                     features_row[f'leaf_temp_lag_{l}'] = history['leaf_temp'].iloc[-l]
                    
# # #             # Creiamo un DataFrame mono-riga per l'inferenza
# # #             df_infer = pd.DataFrame([features_row])
            
# # #             # Predizione della leaf_temp per lo step i-esimo
# # #             pred_leaf = ml_model_pipeline.predict(df_infer)[0]
            
# # #             # --- AGGIORNAMENTO STORICO PER IL PROSSIMO STEP ---
# # #             # Aggiungiamo la riga appena predetta allo storico per poter 
# # #             # calcolare i lag al giro successivo.
# # #             new_history_row = current_env_row.copy()
# # #             new_history_row['leaf_temp'] = pred_leaf
            
# # #             history = pd.concat([history, new_history_row], ignore_index=True)
# # #             # Manteniamo solo la finestra necessaria per non saturare la memoria
# # #             history = history.tail(DEFAULT_LAGS)

# # #         leaf_temp_predictions.append(pred_leaf)

# # #     return leaf_temp_predictions