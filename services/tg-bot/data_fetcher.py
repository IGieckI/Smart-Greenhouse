import pandas as pd
import numpy as np
from influxdb_client import InfluxDBClient
from config import INFLUX_URL, INFLUX_TOKEN, INFLUX_ORG, BUCKET, TZ_ROME, logger




def calculate_vpd_array(air_temp, humidity, leaf_temp) -> float:
    es_leaf = 0.61078 * np.exp((17.27 * leaf_temp) / (leaf_temp + 237.3))
    es_air = 0.61078 * np.exp((17.27 * air_temp) / (air_temp + 237.3))
    ea_air = es_air * (humidity / 100.0)
    return max(0, es_leaf - ea_air)



def calculate_vpd(df: pd.DataFrame) -> pd.DataFrame:
    if all(col in df.columns for col in ['air_temp', 'humidity', 'leaf_temp']):
        df['vpd'] = df.apply(lambda row: calculate_vpd_array(row['air_temp'], row['humidity'], row['leaf_temp']), axis=1)
    return df



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