import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from influxdb_client import InfluxDBClient
from config import INFLUX_URL, INFLUX_TOKEN, INFLUX_ORG, BUCKET, TZ_ROME, logger

def calculate_vpd(df: pd.DataFrame) -> pd.DataFrame:
    """ Unified Vectorized calculation of Air and Leaf VPD, including historical predictions. """
    if 'air_temp' in df.columns and 'humidity' in df.columns:
        svp_air = 0.61078 * np.exp((17.27 * df['air_temp']) / (df['air_temp'] + 237.3))
        avp_air = svp_air * (df['humidity'] / 100.0)
        df['vpd_air'] = np.maximum(0, svp_air - avp_air)
        
        if 'leaf_temp' in df.columns:
            valid_leaf = df['leaf_temp'] > -20.0
            svp_leaf = 0.61078 * np.exp((17.27 * df['leaf_temp']) / (df['leaf_temp'] + 237.3))
            df['vpd_leaf'] = np.where(valid_leaf, np.maximum(0, svp_leaf - avp_air), np.nan)
            df['vpd'] = df['vpd_leaf'] 
        else:
            df['vpd'] = df['vpd_air']

    if 'air_temp_pred' in df.columns and 'humidity_pred' in df.columns:
        svp_air_pred = 0.61078 * np.exp((17.27 * df['air_temp_pred']) / (df['air_temp_pred'] + 237.3))
        avp_pred = svp_air_pred * (df['humidity_pred'] / 100.0)
        df['vpd_air_pred'] = np.maximum(0, svp_air_pred - avp_pred)
        
        if 'leaf_temp_pred' in df.columns:
            svp_leaf_pred = 0.61078 * np.exp((17.27 * df['leaf_temp_pred']) / (df['leaf_temp_pred'] + 237.3))
            df['vpd_leaf_pred'] = np.maximum(0, svp_leaf_pred - avp_pred)
            
    return df

def fetch_history_data(board_id: str, hours: int) -> pd.DataFrame:
    """ Standard fast fetcher for ML Inference. """
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    query = f'''
        from(bucket: "{BUCKET}")
          |> range(start: -{hours}h)
          |> filter(fn: (r) => r._measurement == "sensor_measurements")
          |> filter(fn: (r) => r.id_board == "{board_id}")
          |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
    '''
    try:
        df = client.query_api().query_data_frame(query)
        if isinstance(df, list):
            if not df: return pd.DataFrame()
            df = pd.concat(df, ignore_index=True)
            
        if not df.empty:
            df.set_index('_time', inplace=True)
            df.sort_index(inplace=True)
            if df.index.tz is None:
                df.index = df.index.tz_localize('UTC')
            df.index = df.index.tz_convert(TZ_ROME)
            df = calculate_vpd(df)
        return df
    except Exception as e:
        logger.error(f"InfluxDB history fetch error: {e}")
        return pd.DataFrame()

def fetch_history_with_preds(board_id: str, hours_past: int, hours_future: int = 3, min_window: int = 6) -> pd.DataFrame:
    """ Dedicated fetcher for History Plots. Grabs future data & aligns timestamps. """
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    now = datetime.utcnow()
    start_time = (now - timedelta(hours=hours_past)).strftime('%Y-%m-%dT%H:%M:%SZ')
    stop_time = (now + timedelta(hours=hours_future)).strftime('%Y-%m-%dT%H:%M:%SZ')
    
    # Notice we drop the field filter entirely so nothing gets accidentally left behind
    query = f'''
        from(bucket: "{BUCKET}")
          |> range(start: {start_time}, stop: {stop_time})
          |> filter(fn: (r) => r._measurement == "sensor_measurements")
          |> filter(fn: (r) => r.id_board == "{board_id}")
          |> group(columns: ["_measurement", "id_board", "_field"])
          |> aggregateWindow(every: {min_window}m, fn: mean, createEmpty: false)
          |> group(columns: ["_measurement", "id_board"])
          |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
    '''
    try:
        df = client.query_api().query_data_frame(query)
        if isinstance(df, list):
            if not df: return pd.DataFrame()
            df = pd.concat(df, ignore_index=True)
            
        if not df.empty:
            df.set_index('_time', inplace=True)
            df.sort_index(inplace=True)
            if df.index.tz is None:
                df.index = df.index.tz_localize('UTC')
            df.index = df.index.tz_convert(TZ_ROME)
            df = calculate_vpd(df)
        return df
    except Exception as e:
        logger.error(f"InfluxDB plot fetch error: {e}")
        return pd.DataFrame()

def fetch_available_boards() -> list[str]:
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    query = f'''
        import "influxdata/influxdb/schema"
        schema.tagValues(bucket: "{BUCKET}", tag: "id_board")
    '''
    try:
        result = client.query_api().query(query)
        boards = [record.get_value() for table in result for record in table.records]
        return sorted(boards)
    except Exception as e:
        logger.error(f"InfluxDB board fetch error: {e}")
        return []