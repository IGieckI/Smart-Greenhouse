import os
import pandas as pd
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
from shared_core.preprocessing import identify_leaf_steps, gaussian_weighted_interpolation, clean_anomalies

def sync_clean_bucket(influx_url, influx_token, influx_org):
    """Controlla se ci sono nuovi dati in BUCKET_RAW, li pulisce e li salva in BUCKET_CLEAN."""
    bucket_raw = "sensor_data"
    bucket_clean = "sensor_data_clean"
    
    client = InfluxDBClient(url=influx_url, token=influx_token, org=influx_org)
    
    # 1. Assicurati che il bucket pulito esista
    buckets_api = client.buckets_api()
    if buckets_api.find_bucket_by_name(bucket_clean) is None:
        print(f"[Sync] Bucket '{bucket_clean}' non trovato. Creazione in corso...")
        buckets_api.create_bucket(bucket_name=bucket_clean, org=influx_org)

    # 2. Trova l'ultimo timestamp pulito
    query_api = client.query_api()
    query_last = f'''
        from(bucket: "{bucket_clean}")
          |> range(start: -30d)
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
        from(bucket: "{bucket_raw}")
          {time_filter}
          |> filter(fn: (r) => r._measurement == "sensor_measurements")
          |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
    '''
    df_raw = query_api.query_data_frame(query_raw)
    
    if isinstance(df_raw, list):
        if len(df_raw) == 0: return # Nessun dato nuovo
        df_raw = pd.concat(df_raw, ignore_index=True)
    if df_raw.empty: return

    # Unificazione label TDS
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

        df_board = identify_leaf_steps(df_board, max_gap_minutes=12)
        df_board = gaussian_weighted_interpolation(df_board, 'leaf_temp', weight_col='leaf_weight', win_before=5, win_after=2)
        df_board = clean_anomalies(df_board)

        cols_to_drop = ['result', 'table', '_start', '_stop', '_measurement', 'block_id', 'leaf_weight']
        df_clean = df_board.drop(columns=[c for c in cols_to_drop if c in df_board.columns])

        points = []
        for timestamp, row in df_clean.iterrows():
            p = Point("sensor_measurements").tag("id_board", str(board)).time(timestamp)
            for field, value in row.items():
                if field != 'id_board' and pd.notnull(value):
                    p.field(field, float(value))
            points.append(p)
        
        if points:
            write_api.write(bucket=bucket_clean, org=influx_org, record=points)
            print(f"[Sync] Inseriti {len(points)} nuovi record per board {board}")