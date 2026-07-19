import os
import pandas as pd
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
from shared_core.config import *
from shared_core.preprocessing import apply_board_pipeline

def _get_max_time(query_api, query):
    try:
        res = query_api.query_data_frame(query)
        if isinstance(res, list):
            res = pd.concat(res, ignore_index=True) if len(res) > 0 else pd.DataFrame()
        if res is not None and not res.empty and '_time' in res.columns:
            return res['_time'].max()
    except Exception as e:
        print(f"[Sync] Error extracting max time: {e}")
    return None

def sync_clean_bucket(influx_url, influx_token, influx_org, freq_minutes=6):
    """
        Synchronizes and processes RAW data into a dynamically sampled clean bucket.
        Ensures that forecasting (predictions) from caveaux are re-integrated.
    """
    bucket_clean = f"{BUCKET_CLEAN_PREFIX}{freq_minutes}m"
    client = InfluxDBClient(url=influx_url, token=influx_token, org=influx_org)
    
    buckets_api = client.buckets_api()
    query_api = client.query_api()
    write_api = client.write_api(write_options=SYNCHRONOUS)

    for bucket in [bucket_clean, BUCKET_CAVEAUX]:
        if buckets_api.find_bucket_by_name(bucket) is None:
            print(f"[Sync] Bucket '{bucket}' not found. Creating...")
            buckets_api.create_bucket(bucket_name=bucket, org=influx_org)
            
    query_last_raw = f'''
        from(bucket: "{bucket_clean}")
          |> range(start: {SYNC_LOOKBACK_DAYS})
          |> filter(fn: (r) => r._measurement == "sensor_measurements")
          |> filter(fn: (r) => r._field !~ /pred/)
          |> last()
          |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
    '''
    
    last_time = _get_max_time(query_api, query_last_raw)

    time_filter_raw = "|> range(start: 0)"
    if last_time is not None:
        time_filter_raw = f"|> range(start: {(last_time - pd.Timedelta(minutes=60)).isoformat()})"

    print(f"[Sync {freq_minutes}m] Querying RAW bucket (Time filter: {time_filter_raw})...")
    query_raw = f'''
        from(bucket: "{BUCKET_RAW}")
          {time_filter_raw}
          |> filter(fn: (r) => r._measurement == "sensor_measurements")
          |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
    '''
    
    df_raw = query_api.query_data_frame(query_raw)
    if isinstance(df_raw, list):
        df_raw = pd.concat(df_raw, ignore_index=True) if len(df_raw) > 0 else pd.DataFrame()

    if df_raw is not None and not df_raw.empty:
        print(f"[Sync {freq_minutes}m] Pre-processing {len(df_raw)} raw records...")

        if 'tds_value' in df_raw.columns:
            df_raw['tds'] = df_raw['tds'].combine_first(df_raw['tds_value']) if 'tds' in df_raw.columns else df_raw['tds_value']
            df_raw.drop(columns=['tds_value'], inplace=True, errors='ignore')

        if 'leaf_temperature' in df_raw.columns:
            df_raw['leaf_temp'] = df_raw['leaf_temp'].combine_first(df_raw['leaf_temperature']) if 'leaf_temp' in df_raw.columns else df_raw['leaf_temperature']
            df_raw.drop(columns=['leaf_temperature'], inplace=True, errors='ignore')

        for board in df_raw['id_board'].dropna().unique():
            df_board = df_raw[df_raw['id_board'] == board].copy()
            df_board.set_index('_time', inplace=True)
            df_board.sort_index(inplace=True)

            df_clean = apply_board_pipeline(df_board, board)

            freq_str = f"{freq_minutes}min"
            df_clean = df_clean.resample(freq_str).mean(numeric_only=True)
            
            max_nans_to_fill = max(1, int(MAX_INTERPOLATION_GAP_MINUTES / freq_minutes))
            numeric_cols = df_clean.select_dtypes(include='number').columns
            
            for col in numeric_cols:
                is_na = df_clean[col].isna()
                gap_sizes = is_na.groupby((~is_na).cumsum()).transform('sum')

                df_clean[col] = df_clean[col].interpolate(method='linear').mask(is_na & (gap_sizes > max_nans_to_fill))

            cols_to_drop = ['result', 'table', '_start', '_stop', '_measurement', 'block_id', 'leaf_weight']
            df_clean.drop(columns=[c for c in cols_to_drop if c in df_clean.columns], inplace=True)

            if last_time is not None:
                df_clean = df_clean[df_clean.index > last_time]
            
            df_clean.dropna(how='all', inplace=True)

            points = []
            for timestamp, row in df_clean.iterrows():
                p = Point("sensor_measurements").tag("id_board", str(board)).time(timestamp)
                for field, value in row.items():
                    if pd.notnull(value):
                        p.field(field, float(value))
                points.append(p)
            
            if points:
                write_api.write(bucket=bucket_clean, org=influx_org, record=points)
                print(f"[Sync {freq_minutes}m] Inserted {len(points)} clean records for Board {board}")
    else:
        print(f"[Sync {freq_minutes}m] No new raw data found.")



    print(f"[Sync {freq_minutes}m] Importing historical forecasts from caveaux bucket...")
    
    query_last_pred = f'''
        from(bucket: "{bucket_clean}")
          |> range(start: {SYNC_LOOKBACK_DAYS})
          |> filter(fn: (r) => r._measurement == "sensor_measurements")
          |> filter(fn: (r) => r._field =~ /pred/)
          |> last()
          |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
    '''
    
    last_pred_time = _get_max_time(query_api, query_last_pred)
    

    time_filter_pred = f"|> range(start: {INFERENCE_LOOKBACK_DAYS})"
    if last_time is not None:
        time_filter_pred = f"|> range(start: {(last_pred_time - pd.Timedelta(minutes=60)).isoformat()})"

    query_caveaux = f'''
        from(bucket: "{BUCKET_CAVEAUX}")
          {time_filter_pred}
          |> filter(fn: (r) => r._measurement == "sensor_measurements")
          |> filter(fn: (r) => r.freq == "{freq_minutes}m")
          |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
    '''

    try:
        df_caveaux = query_api.query_data_frame(query_caveaux)
        if isinstance(df_caveaux, list):
            df_caveaux = pd.concat(df_caveaux, ignore_index=True) if len(df_caveaux) > 0 else pd.DataFrame()
            
        if df_caveaux is not None and not df_caveaux.empty:
            cav_points = []
            for _, row in df_caveaux.iterrows():
                p = (Point("sensor_measurements")
                     .tag("id_board", str(row.get('id_board', '')))
                     .tag("model_source", str(row.get('model_source', '')))
                     .tag("freq", str(row.get('freq', f'{freq_minutes}m')))
                     .time(row['_time']))
                
                for pred_field in ['leaf_temp_pred', 'air_temp_pred', 'humidity_pred']:
                    if pred_field in row and pd.notna(row[pred_field]):
                        p.field(pred_field, float(row[pred_field]))
                        
                cav_points.append(p)
                
            if cav_points:
                write_api.write(bucket=bucket_clean, org=influx_org, record=cav_points)
                print(f"[Sync {freq_minutes}m] Re-integrated {len(cav_points)} forecasts by caveaux into {bucket_clean}.")
        else:
            print(f"[Sync {freq_minutes}m] No new forecasting found in caveaux.")
    except Exception as e:
        print(f"[Sync {freq_minutes}m] Error while attempting import from caveaux: {e}")