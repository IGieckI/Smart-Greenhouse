import pandas as pd
import numpy as np
import httpx
from datetime import datetime, timedelta
from influxdb_client import InfluxDBClient
from influxdb_client.client.flux_table import TableList
from config import INFLUX_URL, INFLUX_TOKEN, INFLUX_ORG, BUCKET, TZ_ROME, CONTROLLER_URL, logger


def calculate_svp(t):
    return 0.61078 * np.exp((17.27 * t) / (t + 237.3))


def _compute_vpd_for_cols(df: pd.DataFrame, air_col: str, hum_col: str, leaf_col: str, prefix: str):
    if (air_col in df.columns) and (hum_col in df.columns):
        svp_air = calculate_svp(df[air_col])
        avp_air = svp_air * (df[hum_col] / 100.0)
        df[f'vpd_air{prefix}'] = np.maximum(0, svp_air - avp_air)
        
        if leaf_col in df.columns:
            valid_leaf = df[leaf_col] > -20.0
            svp_leaf = calculate_svp(df[leaf_col])
            df[f'vpd_leaf{prefix}'] = np.where(valid_leaf, np.maximum(0, svp_leaf - avp_air), np.nan)
            if prefix == "":
                df['vpd'] = df[f'vpd_leaf{prefix}']
        else:
            if prefix == "":
                df['vpd'] = df[f'vpd_air{prefix}']


def calculate_vpd(df: pd.DataFrame) -> pd.DataFrame:
    _compute_vpd_for_cols(df, 'air_temp', 'humidity', 'leaf_temp', '')
    _compute_vpd_for_cols(df, 'air_temp_pred', 'humidity_pred', 'leaf_temp_pred', '_pred')
    return df


def _fmt_influx_time(ts) -> str:
    ts = pd.Timestamp(ts)
    if ts.tz is not None:
        ts = ts.tz_convert('UTC').tz_localize(None)
    return ts.strftime('%Y-%m-%dT%H:%M:%SZ')





def _query_history_window(client: InfluxDBClient, board_id: str, start, stop, min_window: int) -> pd.DataFrame:
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
    if (df.empty) or ('_time' not in df.columns):
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
    now = datetime.utcnow()
    try:
        with InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG) as client:
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



def fetch_topology_boards() -> set[str] | None:
    try:
        resp = httpx.get(f"{CONTROLLER_URL}/api/topology", timeout=5.0)
        resp.raise_for_status()
        return set(resp.json().keys())
    except Exception as e:
        logger.error(f"Controller topology fetch error: {e}")
        return None

def fetch_available_boards() -> list[str]:
    query = f'''
        import "influxdata/influxdb/schema"
        schema.tagValues(bucket: "{BUCKET}", tag: "id_board")
    '''
    try:
        with InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG) as client:
            result : TableList = client.query_api().query(query)
            boards = [record.get_value() for table in result for record in table.records]
    except Exception as e:
        logger.error(f"InfluxDB board fetch error: {e}")
        return []

    topology = fetch_topology_boards()
    if topology is not None:
        boards = [b for b in boards if b in topology]
    return sorted(boards)