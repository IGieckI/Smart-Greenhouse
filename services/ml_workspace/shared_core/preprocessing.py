import pandas as pd
import numpy as np
from shared_core.config import *





# ==========================================
# 3. CORE UTILITIES (Gauss & Lags)
# ==========================================
def gaussian_weighted_interpolation(df: pd.DataFrame, target_col: str, weight_col: str = None) -> pd.DataFrame:
    df_out = df.copy()
    nan_indices = df_out[df_out[target_col].isna()].index

    for idx in nan_indices:
        valid_data = df_out.dropna(subset=[target_col])
        before = valid_data.loc[:idx].iloc[-INTERPOLATION_WIN_BEFORE:] if not valid_data.loc[:idx].empty else pd.DataFrame()
        after = valid_data.loc[idx:].iloc[:INTERPOLATION_WIN_AFTER] if not valid_data.loc[idx:].empty else pd.DataFrame()
        
        neighbors = pd.concat([before, after])
        if neighbors.empty: continue

        time_diffs = np.abs((neighbors.index - idx).total_seconds() / 60.0)
        gauss_weights = np.exp(-(time_diffs**2) / (2 * INTERPOLATION_SIGMA_MIN**2))
        
        if weight_col and weight_col in neighbors.columns:
            custom_weights = neighbors[weight_col].values
            final_weights = gauss_weights * custom_weights
        else:
            final_weights = gauss_weights

        if np.sum(final_weights) > 0.01:
            df_out.at[idx, target_col] = np.average(neighbors[target_col], weights=final_weights)

    return df_out


def create_lagged_features(df: pd.DataFrame, target_col: str, feature_cols: list, lags: int = DEFAULT_LAGS, lag_target: bool = True) -> pd.DataFrame:
    df_lagged = df.copy()
    cols_to_lag = feature_cols.copy()
    if lag_target: cols_to_lag.append(target_col)
    
    for col in cols_to_lag:
        for i in range(1, lags + 1):
            # IL SALTO LOGICO: 1 tick = VIRTUAL_RATIO (es. 5 step indietro)
            df_lagged[f'{col}_lag_{i}'] = df_lagged[col].shift(i * VIRTUAL_RATIO)
            
    df_lagged.dropna(inplace=True)
    return df_lagged


def get_extended_features_list(base_features: list, use_lags: bool) -> list:
    ext = base_features.copy()
    ext.extend(['time_sin', 'time_cos'])
    if use_lags:
        ext.extend([f"{col}_diff" for col in base_features])
    return ext


def build_advanced_features(df: pd.DataFrame, base_features: list, use_lags: bool) -> pd.DataFrame:
    df_out = df.copy()
    
    if not isinstance(df_out.index, pd.DatetimeIndex):
        try:
            df_out.index = pd.to_datetime(df_out.index)
        except Exception as e:
            print(f"Errore nella conversione dell'indice in datetime: {e}")
            return df_out

    minutes = df_out.index.hour * 60 + df_out.index.minute
    df_out['time_sin'] = np.sin(2 * np.pi * minutes / 1440)
    df_out['time_cos'] = np.cos(2 * np.pi * minutes / 1440)
    
    if use_lags:
        for col in base_features:
            if col in df_out.columns:
                temp_series = df_out[col].ffill()
                # DERIVATA SUL MACRO-TREND: Calcoliamo la differenza rispetto a 30 minuti fa (o X minuti fa in base al ratio)
                df_out[f'{col}_diff'] = temp_series.diff(VIRTUAL_RATIO)
                
    return df_out







# ==========================================
# 1. FUNZIONI DI PULIZIA SPECIFICHE
# ==========================================

def identify_leaf_steps(df: pd.DataFrame) -> pd.DataFrame:
    """Rileva i 'gradini' di leaf_temp e assegna un peso maggiore ai nuovi valori."""
    if df.empty or 'leaf_temp' not in df.columns:
        return df

    time_diff = df.index.to_series().diff()
    df['block_id'] = (time_diff > pd.Timedelta(minutes=LEAF_MAX_GAP_MINUTES)).cumsum()

    temp_leaf = df['leaf_temp'].ffill() 
    leaf_diff = temp_leaf.groupby(df['block_id']).diff()

    df['leaf_weight'] = np.where((leaf_diff != 0) | (leaf_diff.isna()), 2, 1)
    df.loc[df['leaf_temp'].isna(), 'leaf_weight'] = 1 
    
    return df

def apply_gaussian_interpolation(df: pd.DataFrame) -> pd.DataFrame:
    """Applica l'interpolazione gaussiana a leaf_temp."""
    return gaussian_weighted_interpolation(df, 'leaf_temp', weight_col='leaf_weight')

def clean_anomalies(df: pd.DataFrame) -> pd.DataFrame:
    """Risolve le anomalie di water_temp (<10) e i picchi anomali di TDS."""
    if 'water_temp' in df.columns:
        df.loc[df['water_temp'] < MIN_VALID_WATER_TEMP, 'water_temp'] = np.nan
        df = gaussian_weighted_interpolation(df, 'water_temp')

    if 'tds' in df.columns:
        rolling_median = df['tds'].rolling(window=TDS_ROLLING_WINDOW, center=True, min_periods=1).median()
        
        # Identifica i picchi verso l'alto
        is_high_spike = df['tds'] > (rolling_median * TDS_SPIKE_THRESHOLD)
        # Identifica i crolli verso il basso
        is_low_spike = df['tds'] < (rolling_median * (1 / TDS_SPIKE_THRESHOLD))
        
        # Combina le due condizioni (True se una delle due è vera)
        is_anomaly = is_high_spike | is_low_spike
        
        df.loc[is_anomaly, 'tds'] = np.nan
        df = gaussian_weighted_interpolation(df, 'tds')
        
    return df

def remove_tds_zero(df: pd.DataFrame) -> pd.DataFrame:
    """Sostituisce i valori TDS esattamente a 0 con NaN, per poi interpolarli."""
    if 'tds' in df.columns:
        df.loc[df['tds'] < 60, 'tds'] = np.nan
        df = gaussian_weighted_interpolation(df, 'tds')
    return df

# ==========================================
# 2. PIPELINE DEDICATE (STRATEGY PATTERN)
# ==========================================
BOARD_PIPELINES = {
    BOARD_324: [
        identify_leaf_steps,
        apply_gaussian_interpolation,
        clean_anomalies  # Ripristinato il vecchio nome
    ],
    BOARD_944: [
        remove_tds_zero, 
        identify_leaf_steps,
        apply_gaussian_interpolation,
        clean_anomalies
    ]
}

def apply_board_pipeline(df: pd.DataFrame, board_id: str) -> pd.DataFrame:
    """Esegue dinamicamente tutte le funzioni di pulizia associate a una specifica board."""
    pipeline = BOARD_PIPELINES.get(board_id, BOARD_PIPELINES[BOARD_324]) # Fallback sulla 324
    
    df_processed = df.copy()
    for step_function in pipeline:
        df_processed = step_function(df_processed)
        
    return df_processed
