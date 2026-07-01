import pandas as pd
import numpy as np
from shared_core.config import *


# CORE UTILITIES
def gaussian_weighted_interpolation(df: pd.DataFrame, target_col: str, weight_col: str = None) -> pd.DataFrame:
    df_out = df.copy()
    nan_indices = df_out[df_out[target_col].isna()].index

    for idx in nan_indices:
        valid_data = df_out.dropna(subset=[target_col])
        before = valid_data.loc[:idx].iloc[-INTERPOLATION_WIN_BEFORE:] if not valid_data.loc[:idx].empty else pd.DataFrame()
        after = valid_data.loc[idx:].iloc[:INTERPOLATION_WIN_AFTER] if not valid_data.loc[idx:].empty else pd.DataFrame()
        
        # --- NEW LOGIC: Check hole size ---
        if not before.empty and not after.empty:
            gap_minutes = (after.index[0] - before.index[-1]).total_seconds() / 60.0
            if gap_minutes > MAX_INTERPOLATION_GAP_MINUTES:
                continue # Hole too large, do not interpolate (leave NaN)
        elif not before.empty: # Edge case (end of dataset)
            if (idx - before.index[-1]).total_seconds() / 60.0 > MAX_INTERPOLATION_GAP_MINUTES:
                continue
        elif not after.empty: # Edge case (start of dataset)
            if (after.index[0] - idx).total_seconds() / 60.0 > MAX_INTERPOLATION_GAP_MINUTES:
                continue
        # --------------------------------------------------------

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

def create_lagged_features(df: pd.DataFrame, target_col: str, feature_cols: list, virtual_ratio: int, lags: int = DEFAULT_LAGS, lag_target: bool = True) -> pd.DataFrame:
    cols_to_lag = feature_cols.copy()
    if lag_target: 
        cols_to_lag.append(target_col)
    
    # Dictionary to accumulate new shifted columns without fragmenting memory
    lagged_data = {}
    
    for col in cols_to_lag:
        if col in df.columns:
            for i in range(1, lags + 1):
                lagged_data[f'{col}_lag_{i}'] = df[col].shift(i * virtual_ratio)
                
    # If columns were generated, we merge them with the original DataFrame in bulk
    if lagged_data:
        df_lagged = pd.concat([df, pd.DataFrame(lagged_data, index=df.index)], axis=1)
    else:
        df_lagged = df.copy()
        
    # Apply dropna on the final combined DataFrame
    df_lagged.dropna(inplace=True)
    return df_lagged

def get_extended_features_list(base_features: list, use_lags: bool) -> list:
    ext = base_features.copy()
    ext.extend(['time_sin', 'time_cos'])
    if use_lags:
        ext.extend([f"{col}_diff" for col in base_features])
    return ext


def build_advanced_features(df: pd.DataFrame, base_features: list, use_lags: bool, virtual_ratio: int) -> pd.DataFrame:
    df_out : pd.DataFrame = df.copy()
    
    if not isinstance(df_out.index, pd.DatetimeIndex):
        try:
            df_out.index = pd.to_datetime(df_out.index)
        except Exception as e:
            print(f"Error converting index to datetime: {e}")
            return df_out

    minutes = df_out.index.hour * 60 + df_out.index.minute
    df_out['time_sin'] = np.sin(2 * np.pi * minutes / 1440)
    df_out['time_cos'] = np.cos(2 * np.pi * minutes / 1440)
    
    if use_lags:
        for col in base_features:
            if col in df_out.columns:
                temp_series = df_out[col].ffill()
                df_out[f'{col}_diff'] = temp_series.diff(virtual_ratio)
                
    return df_out



# CLEANING FUNCTIONS
def identify_leaf_steps(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or 'leaf_temp' not in df.columns:
        return df

    time_diff = df.index.to_series().diff()
    df['block_id'] = (time_diff > pd.Timedelta(minutes=LEAF_MAX_GAP_MINUTES)).cumsum()

    temp_leaf = df['leaf_temp'].ffill() 
    leaf_diff = temp_leaf.groupby(df['block_id']).diff()

    df['leaf_weight'] = np.where((leaf_diff != 0) | (leaf_diff.isna()), 2, 1)
    df.loc[df['leaf_temp'].isna(), 'leaf_weight'] = 1 
    return df

def apply_leaf_gaussian_interpolation(df: pd.DataFrame) -> pd.DataFrame:
    if 'leaf_temp' not in df.columns:
        return df
    return gaussian_weighted_interpolation(df, 'leaf_temp', weight_col='leaf_weight')


def clean_anomalies(df: pd.DataFrame) -> pd.DataFrame:
    if 'water_temp' in df.columns:
        df.loc[df['water_temp'] < MIN_VALID_WATER_TEMP, 'water_temp'] = np.nan
        df = gaussian_weighted_interpolation(df, 'water_temp')

    if 'tds' in df.columns:
        rolling_median = df['tds'].rolling(window=TDS_ROLLING_WINDOW, center=True, min_periods=1).median()
        is_high_spike = df['tds'] > (rolling_median * TDS_SPIKE_THRESHOLD)
        is_low_spike = df['tds'] < (rolling_median * (1 / TDS_SPIKE_THRESHOLD))
        
        df.loc[is_high_spike | is_low_spike, 'tds'] = np.nan
        df = gaussian_weighted_interpolation(df, 'tds')
    return df

def remove_tds_zero(df: pd.DataFrame) -> pd.DataFrame:
    if 'tds' in df.columns:
        df.loc[df['tds'] < 60, 'tds'] = np.nan
        df = gaussian_weighted_interpolation(df, 'tds')
    return df



# BOARD PIPELINES
BOARD_PIPELINES = {
    BOARD_324: [identify_leaf_steps, apply_leaf_gaussian_interpolation, clean_anomalies],
    BOARD_944: [remove_tds_zero, apply_leaf_gaussian_interpolation, clean_anomalies]
}

def apply_board_pipeline(df: pd.DataFrame, board_id: str) -> pd.DataFrame:
    pipeline = BOARD_PIPELINES.get(board_id, BOARD_PIPELINES[BOARD_324])
    df_processed = df.copy()
    for step_function in pipeline:
        df_processed = step_function(df_processed)
    return df_processed