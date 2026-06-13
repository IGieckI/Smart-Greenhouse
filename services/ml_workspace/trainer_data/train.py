import os
import pandas as pd
from influxdb_client import InfluxDBClient
from sklearn.linear_model import LinearRegression
import joblib
import time

INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
INFLUX_ORG = os.getenv("INFLUX_ORG")
MODEL_PATH = "/app/models/leaf_temp_model.pkl"

def train_model():
    print("[Trainer] Avvio processo di training...")
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    query_api = client.query_api()

    # Estrae gli ultimi 7 giorni di dati
    query = f"""
    from(bucket: "sensor_data")
      |> range(start: -7d)
      |> filter(fn: (r) => r._measurement == "sensor_measurements")
      |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
    """
    
    try:
        # In un contesto reale, qui ci sarebbe una logica complessa di preprocessing
        df = query_api.query_data_frame(query)
        # BUG FIX: Usa i nomi esatti salvati dal controller
        if df.empty or 'air_temp' not in df.columns or 'leaf_temp' not in df.columns:
            print("[Trainer] Dati insufficienti per il training.")
            return

        df = df.dropna(subset=['air_temp', 'leaf_temp'])

        X = df[['air_temp']]
        y = df['leaf_temp']

        model = LinearRegression()
        model.fit(X, y)

        joblib.dump(model, MODEL_PATH)
        print(f"[Trainer] Modello salvato con successo in {MODEL_PATH}")

    except Exception as e:
        print(f"[Trainer] Errore durante il training: {e}")

if __name__ == "__main__":
    # Per il PoC, attende che Influx abbia dati e poi addestra una volta
    time.sleep(30)
    train_model()