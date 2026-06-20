import os
import pandas as pd
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
import sys

# Importiamo dal path condiviso di Docker
sys.path.append('/app')
from shared_core.preprocessing import identify_leaf_steps, gaussian_weighted_interpolation, clean_anomalies

INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
INFLUX_ORG = os.getenv("INFLUX_ORG", "iot_org")
BUCKET_RAW = "sensor_data"
BUCKET_CLEAN = "sensor_data_clean"

def ensure_bucket_exists(client, bucket_name, org_name):
    """Controlla se il bucket esiste, altrimenti lo crea automaticamente."""
    buckets_api = client.buckets_api()
    
    # Cerca il bucket per nome
    bucket = buckets_api.find_bucket_by_name(bucket_name)
    
    if bucket is None:
        print(f"[Cleaner] Bucket '{bucket_name}' non trovato. Creazione in corso...")
        # Se non esiste, lo crea assegnandolo alla nostra organizzazione
        buckets_api.create_bucket(bucket_name=bucket_name, org=org_name)
        print(f"[Cleaner] Bucket '{bucket_name}' creato con successo!")
    else:
        print(f"[Cleaner] Bucket '{bucket_name}' già esistente. Procedo...")

def get_last_clean_timestamp(client):
    """Cerca qual è l'ultimo dato pulito inserito per sapere da dove ripartire."""
    query_api = client.query_api()
    # Aggiunto il pivot() richiesto dal Warning
    query = f'''
        from(bucket: "{BUCKET_CLEAN}")
          |> range(start: -30d)
          |> filter(fn: (r) => r._measurement == "sensor_measurements")
          |> last()
          |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
    '''
    try:
        result = query_api.query_data_frame(query)
        
        # Gestione sicura del formato lista
        if isinstance(result, list):
            if len(result) == 0:
                return None
            result = pd.concat(result, ignore_index=True)
            
        if not result.empty and '_time' in result.columns:
            return result['_time'].max()
    except Exception:
        pass
    return None

def main():
    print("[Cleaner] Avvio pipeline di pulizia...")
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    
    # 0. Controlla e crea il bucket se necessario PRIMA di fare qualsiasi cosa
    ensure_bucket_exists(client, BUCKET_CLEAN, INFLUX_ORG)
    
    last_time = get_last_clean_timestamp(client)
    time_filter = f"|> range(start: {last_time.isoformat()})" if last_time else '|> range(start: 0)'

    query = f'''
        from(bucket: "{BUCKET_RAW}")
          {time_filter}
          |> filter(fn: (r) => r._measurement == "sensor_measurements")
          |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
    '''
    
    df_raw = client.query_api().query_data_frame(query)
    
    # NOVITÀ: Gestione del caso in cui Influx restituisca una lista
    if isinstance(df_raw, list):
        if len(df_raw) == 0:  # Lista vuota (nessun dato)
            print("[Cleaner] Nessun nuovo dato da pulire.")
            return
        # Unisce tutti i DataFrame della lista in uno solo
        df_raw = pd.concat(df_raw, ignore_index=True)
    
    if df_raw.empty:
        print("[Cleaner] Nessun nuovo dato da pulire.")
        return
    
    # --- INIZIO UNIFICAZIONE LABEL TDS ---
    if 'tds_value' in df_raw.columns:
        print("[Cleaner] Rilevata colonna 'tds_value', unificazione con 'tds' in corso...")
        
        if 'tds' in df_raw.columns:
            # Se esistono entrambe le colonne: diamo priorità a 'tds', se è NaN usiamo il valore di 'tds_value'
            df_raw['tds'] = df_raw['tds'].combine_first(df_raw['tds_value'])
        else:
            # Se esiste solo 'tds_value', rinominiamo la colonna
            df_raw.rename(columns={'tds_value': 'tds'}, inplace=True)
            
        # Rimuoviamo la colonna vecchia per evitare di salvarla nel bucket pulito
        if 'tds_value' in df_raw.columns:
            df_raw.drop(columns=['tds_value'], inplace=True)
    # --- FINE UNIFICAZIONE LABEL TDS ---
    
    # Processiamo ogni board separatamente
    boards = df_raw['id_board'].unique()
    write_api = client.write_api(write_options=SYNCHRONOUS)

    for board in boards:
        df_board = df_raw[df_raw['id_board'] == board].copy()
        df_board.set_index('_time', inplace=True)
        df_board.sort_index(inplace=True)

        print(f"[Cleaner] Pulizia Board {board} ({len(df_board)} righe)...")

        # 1. Prepariamo leaf_temp
        df_board = identify_leaf_steps(df_board, max_gap_minutes=12)
        df_board = gaussian_weighted_interpolation(df_board, 'leaf_temp', weight_col='leaf_weight', win_before=5, win_after=2)

        # 2. Ripariamo le anomalie su water_temp e tds
        df_board = clean_anomalies(df_board)

        # 3. Scriviamo su BUCKET_CLEAN (Rimuoviamo colonne inutili per il DB)
        cols_to_drop = ['result', 'table', '_start', '_stop', '_measurement', 'block_id', 'leaf_weight']
        df_clean = df_board.drop(columns=[c for c in cols_to_drop if c in df_board.columns])

        # Convertiamo il DF in Points di InfluxDB
        points = []
        for timestamp, row in df_clean.iterrows():
            p = Point("sensor_measurements").tag("id_board", str(board)).time(timestamp)
            for field, value in row.items():
                if field != 'id_board' and pd.notnull(value):
                    # CORREZIONE: Usa .field() al posto di .floatField()
                    p.field(field, float(value))
            points.append(p)

        write_api.write(bucket=BUCKET_CLEAN, org=INFLUX_ORG, record=points)
        print(f"[Cleaner] Board {board} salvata in {BUCKET_CLEAN}.")

if __name__ == "__main__":
    main()