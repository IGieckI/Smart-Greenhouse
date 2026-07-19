import pandas as pd
import numpy as np
import httpx
from datetime import datetime, timedelta
from influxdb_client import InfluxDBClient
from config import INFLUX_URL, INFLUX_TOKEN, INFLUX_ORG, BUCKET, TZ_ROME, CONTROLLER_URL, logger



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



def _fmt_influx_time(ts) -> str:
    ts = pd.Timestamp(ts)
    if ts.tz is not None:
        ts = ts.tz_convert('UTC').tz_localize(None)
    return ts.strftime('%Y-%m-%dT%H:%M:%SZ')




def _query_history_window(client: InfluxDBClient, board_id: str, start, stop, min_window: int) -> pd.DataFrame:
    """ Aggregated, pivoted history for a board over an explicit [start, stop] window. """
    query = f'''
        from(bucket: "{BUCKET}")
          |> range(start: {_fmt_influx_time(start)}, stop: {_fmt_influx_time(stop)})
          |> filter(fn: (r) => r._measurement == "sensor_measurements")
          |> filter(fn: (r) => r.id_board == "{board_id}")
          |> group(columns: ["_measurement", "id_board", "_field"])
          |> aggregateWindow(every: {min_window}m, fn: mean, createEmpty: false)
          |> group(columns: ["_measurement", "id_board"])
          |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
    '''
    df = client.query_api().query_data_frame(query)
    if isinstance(df, list):
        if not df:
            return pd.DataFrame()
        df = pd.concat(df, ignore_index=True)

    if not df.empty:
        df.set_index('_time', inplace=True)
        df.sort_index(inplace=True)
        if df.index.tz is None:
            df.index = df.index.tz_localize('UTC')
        df.index = df.index.tz_convert(TZ_ROME)
        df = calculate_vpd(df)
    return df

def _latest_board_timestamp(client: InfluxDBClient, board_id: str, lookback_days: int = 180):
    """ Most recent reading time for a board, or None if it has no data at all. """
    query = f'''
        from(bucket: "{BUCKET}")
          |> range(start: -{lookback_days}d)
          |> filter(fn: (r) => r._measurement == "sensor_measurements")
          |> filter(fn: (r) => r.id_board == "{board_id}")
          |> keep(columns: ["_time"])
          |> max(column: "_time")
    '''
    df = client.query_api().query_data_frame(query)
    if isinstance(df, list):
        if not df:
            return None
        df = pd.concat(df, ignore_index=True)
    if df.empty or '_time' not in df.columns:
        return None
    return pd.Timestamp(df['_time'].max())





def fetch_history_data(board_id: str, hours: int) -> pd.DataFrame:
    query = f'''
        from(bucket: "{BUCKET}")
          |> range(start: -{hours}h)
          |> filter(fn: (r) => r._measurement == "sensor_measurements")
          |> filter(fn: (r) => r.id_board == "{board_id}")
          |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
    '''
    try:
        with InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG) as client:
            df = client.query_api().query_data_frame(query)
        if isinstance(df, list):
            if not df:
                return pd.DataFrame()
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
    """ Dedicated fetcher for History Plots. Grabs future data & aligns timestamps.

    Anchors the window to 'now'. If the board is lagging/offline and that window is
    empty, falls back to the most recent `hours_past` of data that actually exists,
    so a stale board still renders its latest history instead of reporting "no data".
    """
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    now = datetime.utcnow()
    try:
        df = _query_history_window(
            client, board_id,
            now - timedelta(hours=hours_past),
            now + timedelta(hours=hours_future),
            min_window,
        )
        if df.empty:
            latest = _latest_board_timestamp(client, board_id)
            if latest is not None:
                logger.info(f"Board {board_id} has no data in the last {hours_past}h; "
                            f"falling back to latest available data at {latest}.")
                df = _query_history_window(
                    client, board_id,
                    latest - timedelta(hours=hours_past),
                    latest + timedelta(hours=hours_future),
                    min_window,
                )
        return df
    except Exception as e:
        logger.error(f"InfluxDB plot fetch error: {e}")
        return pd.DataFrame()
    finally:
        client.close()



def fetch_topology_boards() -> set[str] | None:
    """ Node (board) IDs registered in the controller's topology, or None if unreachable. """
    try:
        resp = httpx.get(f"{CONTROLLER_URL}/api/topology", timeout=5.0)
        resp.raise_for_status()
        return set(resp.json().keys())
    except Exception as e:
        logger.error(f"Controller topology fetch error: {e}")
        return None

def fetch_available_boards() -> list[str]:
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    query = f'''
        import "influxdata/influxdb/schema"
        schema.tagValues(bucket: "{BUCKET}", tag: "id_board")
    '''
    try:
        result = client.query_api().query(query)
        boards = [record.get_value() for table in result for record in table.records]
    except Exception as e:
        logger.error(f"InfluxDB board fetch error: {e}")
        return []

    topology = fetch_topology_boards()
    if topology is not None:
        boards = [b for b in boards if b in topology]
    return sorted(boards)