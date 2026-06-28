import os
import pandas as pd
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

from shared_core.config import *
from shared_core.preprocessing import apply_board_pipeline 

# IMPORTANTE: Parametro freq_minutes aggiunto
def sync_clean_bucket(influx_url, influx_token, influx_org, freq_minutes=6):
    """Sincronizza e processa i dati RAW verso un bucket con campionamento dinamico."""
    
    # Nome dinamico del DB di destinazione!
    bucket_clean = f"{BUCKET_CLEAN_PREFIX}{freq_minutes}m"
    
    client = InfluxDBClient(url=influx_url, token=influx_token, org=influx_org)
    
    # 1. Assicurati che il bucket pulito dinamico esista
    buckets_api = client.buckets_api()
    if buckets_api.find_bucket_by_name(bucket_clean) is None:
        print(f"[Sync] Bucket '{bucket_clean}' non trovato. Creazione in corso...")
        buckets_api.create_bucket(bucket_name=bucket_clean, org=influx_org)

    # 2. Trova l'ultimo timestamp pulito da questo specifico bucket
    query_api = client.query_api()
    query_last = f'''
        from(bucket: "{bucket_clean}")
          |> range(start: {SYNC_LOOKBACK_DAYS})
          |> filter(fn: (r) => r._measurement == "sensor_measurements")
          |> last()
          |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
    '''
    last_time = None
    try:
        res = query_api.query_data_frame(query_last)
        if isinstance(res, list) and len(res) > 0:
            res = pd.concat(res, ignore_index=True)
        if res is not None and not res.empty and '_time' in res.columns:
            last_time = res['_time'].max()
    except Exception:
        pass

    # 3. Estrai i nuovi dati dal RAW
    time_filter = f"|> range(start: {last_time.isoformat()})" if last_time else '|> range(start: 0)'
    query_raw = f'''
        from(bucket: "{BUCKET_RAW}")
          {time_filter}
          |> filter(fn: (r) => r._measurement == "sensor_measurements")
          |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
    '''
    df_raw = query_api.query_data_frame(query_raw)
    
    if isinstance(df_raw, list):
        if len(df_raw) == 0: return # Nessun dato nuovo
        df_raw = pd.concat(df_raw, ignore_index=True)
    if df_raw.empty: return

    if 'tds_value' in df_raw.columns:
        if 'tds' in df_raw.columns:
            df_raw['tds'] = df_raw['tds'].combine_first(df_raw['tds_value'])
        else:
            df_raw.rename(columns={'tds_value': 'tds'}, inplace=True)
        df_raw.drop(columns=['tds_value'], inplace=True, errors='ignore')

    # 4. Processa e Scrivi
    write_api = client.write_api(write_options=SYNCHRONOUS)
    for board in df_raw['id_board'].unique():
        df_board = df_raw[df_raw['id_board'] == board].copy()
        df_board.set_index('_time', inplace=True)
        df_board.sort_index(inplace=True)

        # Regolarizzazione temporale parametrica
        freq_str = f"{freq_minutes}min"
        df_board = df_board.resample(freq_str).mean(numeric_only=True)
        df_board['id_board'] = board 

        # A. Esegui la pipeline sofisticata (Gaussiana) prima, in presenza dei gap strutturali
        df_clean = apply_board_pipeline(df_board, board)
        
        # B. NOVITÀ: Interpolazione lineare per sistemare le variabili ambientali 
        # (es. air_temp) sui micro-buchi creati passando da 6m a 2m
        df_clean = df_clean.interpolate(method='linear', limit_direction='both')

        cols_to_drop = ['result', 'table', '_start', '_stop', '_measurement', 'block_id', 'leaf_weight']
        df_clean = df_clean.drop(columns=[c for c in cols_to_drop if c in df_clean.columns])
        df_clean.dropna(how='all', subset=[c for c in df_clean.columns if c != 'id_board'], inplace=True)

        points = []
        for timestamp, row in df_clean.iterrows():
            p = Point("sensor_measurements").tag("id_board", str(board)).time(timestamp)
            for field, value in row.items():
                if field != 'id_board' and pd.notnull(value):
                    p.field(field, float(value))
            points.append(p)
        
        if points:
            write_api.write(bucket=bucket_clean, org=influx_org, record=points)
            print(f"[Sync {freq_minutes}m] Inseriti {len(points)} record (Board {board})")