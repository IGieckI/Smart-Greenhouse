import pandas as pd
import numpy as np


def create_lagged_features(df: pd.DataFrame, target_col: str, feature_cols: list, lags: int = 6) -> pd.DataFrame:
    """
    Crea le feature ritardate (t-1, t-2, ... t-lags) per il modello Autoregressivo (V2).
    In Pandas, usare .shift(i) sposta i dati in avanti, allineando i valori passati alla riga attuale.
    """
    df_lagged = df.copy()
    
    # Colonne da 'ritardare' (tutti i sensori ambientali + la leaf_temp passata)
    cols_to_lag = feature_cols + [target_col]
    
    for col in cols_to_lag:
        for i in range(1, lags + 1):
            # Crea nuove colonne tipo: 'air_temp_lag_1', 'leaf_temp_lag_3' ecc.
            df_lagged[f'{col}_lag_{i}'] = df_lagged[col].shift(i)
            
    # Rimuoviamo le prime righe che avranno inevitabilmente dei NaN a causa dello shift
    df_lagged.dropna(inplace=True)
    return df_lagged

def identify_leaf_steps(df: pd.DataFrame, max_gap_minutes: int = 12) -> pd.DataFrame:
    """
    Identifica i blocchi continui, rileva i "gradini" di leaf_temp 
    e assegna il peso (2 per i gradini/nuovi dati, 1 per i ripetuti).
    """
    if df.empty or 'leaf_temp' not in df.columns:
        return df

    # 1. Identifica i blocchi temporali (interruzioni > max_gap_minutes)
    time_diff = df.index.to_series().diff()
    df['block_id'] = (time_diff > pd.Timedelta(minutes=max_gap_minutes)).cumsum()

    # 2. Identifica i cambiamenti di leaf_temp (gradini)
    # Riempiamo temporaneamente i NaN per non rompere il calcolo delle differenze
    temp_leaf = df['leaf_temp'].ffill() 
    leaf_diff = temp_leaf.groupby(df['block_id']).diff()

    # 3. Assegna i pesi (il primo elemento del blocco è NaN nel diff, lo consideriamo "nuovo")
    df['leaf_weight'] = np.where((leaf_diff != 0) | (leaf_diff.isna()), 2, 1)
    
    # Rimuoviamo il peso se il dato originale era assente
    df.loc[df['leaf_temp'].isna(), 'leaf_weight'] = 1 
    
    return df

def gaussian_weighted_interpolation(df: pd.DataFrame, target_col: str, weight_col: str = None, 
                                    win_before: int = 5, win_after: int = 2, sigma_minutes: float = 15.0):
    """
    Applica un'interpolazione basata su media pesata gaussiana asimmetrica per riempire i NaN.
    """
    df_out = df.copy()
    nan_indices = df_out[df_out[target_col].isna()].index

    for idx in nan_indices:
        # Estrai la finestra temporale reale (senza NaN)
        valid_data = df_out.dropna(subset=[target_col])
        
        # Sostituisci la vecchia definizione di 'before' e 'after' con questa:
        before = valid_data.loc[:idx].iloc[-win_before:] if not valid_data.loc[:idx].empty else pd.DataFrame()
        after = valid_data.loc[idx:].iloc[:win_after] if not valid_data.loc[idx:].empty else pd.DataFrame()
        
        neighbors = pd.concat([before, after])
        if neighbors.empty:
            continue

        # Calcola la distanza temporale in minuti
        time_diffs = np.abs((neighbors.index - idx).total_seconds() / 60.0)
        
        # Calcola il peso gaussiano
        # Formula: W_gauss = exp(-(delta_t^2) / (2 * sigma^2))
        gauss_weights = np.exp(-(time_diffs**2) / (2 * sigma_minutes**2))
        
        # Applica il moltiplicatore di peso custom (se definito, es. leaf_weight)
        if weight_col and weight_col in neighbors.columns:
            custom_weights = neighbors[weight_col].values
            final_weights = gauss_weights * custom_weights
        else:
            final_weights = gauss_weights

        # Calcola la media pesata
        if np.sum(final_weights) > 0:
            weighted_mean = np.average(neighbors[target_col], weights=final_weights)
            df_out.at[idx, target_col] = weighted_mean

    return df_out

def clean_anomalies(df: pd.DataFrame) -> pd.DataFrame:
    """Risolve le anomalie di water_temp e tds."""
    # 1. water_temp: rimuove valori < 10°C
    if 'water_temp' in df.columns:
        df.loc[df['water_temp'] < 10, 'water_temp'] = np.nan
        df = gaussian_weighted_interpolation(df, 'water_temp', win_before=5, win_after=2)

    # 2. tds: rimuove spike (outlier locali basati su mediana mobile)
    if 'tds' in df.columns:
        rolling_median = df['tds'].rolling(window=10, center=True, min_periods=1).median()
        # Se il valore eccede il 30% della mediana locale, è uno spike
        is_spike = df['tds'] > (rolling_median * 1.3)
        df.loc[is_spike, 'tds'] = np.nan
        df = gaussian_weighted_interpolation(df, 'tds', win_before=5, win_after=2)
        
    return df

def create_virtual_datasets(df: pd.DataFrame, target_freq_min: int = 30, orig_freq_min: int = 5) -> list[pd.DataFrame]:
    virtual_datasets = []
    num_shifts = target_freq_min // orig_freq_min

    for i in range(num_shifts):
        # Shiftiamo l'indice in avanti di i * orig_freq_min
        shifted_df = df.copy()
        shift_delta = pd.Timedelta(minutes=i * orig_freq_min)
        shifted_df.index = shifted_df.index + shift_delta
        
        # Ora facciamo il resample pulito sulla mezz'ora spaccata
        df_resampled = shifted_df.resample(f'{target_freq_min}min').first().dropna(subset=['leaf_temp'])
        df_resampled['virtual_id'] = i
        virtual_datasets.append(df_resampled)

    return virtual_datasets