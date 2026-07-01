import os
import pandas as pd
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

from shared_core.config import *
from shared_core.preprocessing import apply_board_pipeline 

def sync_clean_bucket(influx_url, influx_token, influx_org, freq_minutes=6):
    """Synchronizes and processes RAW data into a dynamically sampled clean bucket."""
    
    bucket_clean = f"{BUCKET_CLEAN_PREFIX}{freq_minutes}m"
    client = InfluxDBClient(url=influx_url, token=influx_token, org=influx_org)
    
    buckets_api = client.buckets_api()
    if buckets_api.find_bucket_by_name(bucket_clean) is None:
        print(f"[Sync] Bucket '{bucket_clean}' not found. Creating...")
        buckets_api.create_bucket(bucket_name=bucket_clean, org=influx_org)

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

    if last_time:
        overlap_time = last_time - pd.Timedelta(minutes=60)
        time_filter = f"|> range(start: {overlap_time.isoformat()})"
    else:
        time_filter = '|> range(start: 0)'

    print(f"[Sync {freq_minutes}m] Querying RAW bucket (Time filter: {time_filter})...")
    query_raw = f'''
        from(bucket: "{BUCKET_RAW}")
          {time_filter}
          |> filter(fn: (r) => r._measurement == "sensor_measurements")
          |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
    '''
    
    df_raw = query_api.query_data_frame(query_raw)
    
    if isinstance(df_raw, list):
        if len(df_raw) == 0: 
            print(f"[Sync {freq_minutes}m] No new raw data found.")
            return
        df_raw = pd.concat(df_raw, ignore_index=True)
    if df_raw.empty:
        print(f"[Sync {freq_minutes}m] No new raw data found.")
        return
    
    print(f"[Sync {freq_minutes}m] Pre-processing {len(df_raw)} raw records (Standardizing column names)...")
    if 'tds_value' in df_raw.columns:
        if 'tds' in df_raw.columns: df_raw['tds'] = df_raw['tds'].combine_first(df_raw['tds_value'])
        else: df_raw.rename(columns={'tds_value': 'tds'}, inplace=True)
        df_raw.drop(columns=['tds_value'], inplace=True, errors='ignore')

    if 'leaf_temperature' in df_raw.columns:
        if 'leaf_temp' in df_raw.columns: df_raw['leaf_temp'] = df_raw['leaf_temp'].combine_first(df_raw['leaf_temperature'])
        else: df_raw.rename(columns={'leaf_temperature': 'leaf_temp'}, inplace=True)
        df_raw.drop(columns=['leaf_temperature'], inplace=True, errors='ignore')
    

    write_api = client.write_api(write_options=SYNCHRONOUS)
    for board in df_raw['id_board'].unique():
        df_board = df_raw[df_raw['id_board'] == board].copy()
        df_board.set_index('_time', inplace=True)
        df_board.sort_index(inplace=True)

        # --- FIX 1: Apply the pipeline on HIGH-DENSITY RAW data ---
        df_clean = apply_board_pipeline(df_board, board)

        # --- FIX 1B: Resampling occurs AFTER Gaussian cleaning ---
        freq_str = f"{freq_minutes}min"
        df_clean = df_clean.resample(freq_str).mean(numeric_only=True)
        df_clean['id_board'] = board

        # --- FIX 4: The linear "universal sweeper" on resampled data ---
        max_nans_to_fill = max(1, int(MAX_INTERPOLATION_GAP_MINUTES / freq_minutes))
        df_clean = df_clean.infer_objects(copy=False).interpolate(method='linear', limit=max_nans_to_fill)
        
        cols_to_drop = ['result', 'table', '_start', '_stop', '_measurement', 'block_id', 'leaf_weight']
        df_clean.drop(columns=[c for c in cols_to_drop if c in df_clean.columns], inplace=True)

        # Remove data older than last_time (if they exist) because we already have them in the DB
        # (The overlap was only for math, we don't want to reinsert them all,
        # even though InfluxDB handles overwrites well).
        if last_time:
            df_clean = df_clean[df_clean.index > last_time]
        
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
            print(f"[Sync {freq_minutes}m] Inserted {len(points)} clean records for Board {board}")