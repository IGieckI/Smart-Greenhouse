import pandas as pd
from datetime import datetime, timezone

def convert_to_influx_pandas(input_filepath, output_filepath):
    # 1. Lettura del CSV
    try:
        df = pd.read_csv(input_filepath)
    except FileNotFoundError:
        print(f"Errore: Il file {input_filepath} non è stato trovato.")
        return

    if df.empty:
        print("Il file CSV è vuoto.")
        return

    # 2. Unpivoting (Melt): trasforma i sensori da colonne a righe
    # id_vars: colonne da mantenere fisse
    # var_name: nome della nuova colonna che conterrà i nomi delle variabili (es. 'air_temp')
    # value_name: nome della colonna che conterrà i valori misurati
    df_melted = pd.melt(
        df, 
        id_vars=['timestamp', 'node_id'], 
        var_name='_field', 
        value_name='_value'
    )

    # 3. Formattazione vettoriale e aggiunta delle colonne fisse
    # Converte l'intera colonna timestamp nel formato RFC3339 in modo ottimizzato
    df_melted['_time'] = pd.to_datetime(df_melted['timestamp']).dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    
    # Assegna un indice numerico incrementale per ogni tipologia di sensore (la colonna 'table' di Influx)
    df_melted['table'] = df_melted.groupby('_field').ngroup()
    
    # Popoliamo le colonne statiche
    df_melted['result'] = ''
    df_melted['_start'] = "1970-01-01T00:00:00Z"
    df_melted['_stop'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    df_melted['_measurement'] = "sensor_measurements"
    df_melted['id_board'] = df_melted['node_id']
    df_melted['annotation'] = '' # Usata per la prima colonna vuota di InfluxDB

    # 4. Riordino delle colonne per farle combaciare con le specifiche Influx
    final_columns = [
        'annotation', 'result', 'table', '_start', '_stop', 
        '_time', '_value', '_field', '_measurement', 'id_board'
    ]
    df_final = df_melted[final_columns]

    # 5. Scrittura del file
    with open(output_filepath, mode='w', encoding='utf-8') as f:
        # Scrittura manuale degli header Influx (le direttive di annotazione)
        f.write("#group,false,false,true,true,false,false,true,true,true\n")
        f.write("#datatype,string,long,dateTime:RFC3339,dateTime:RFC3339,dateTime:RFC3339,double,string,string,string\n")
        f.write("#default,_result,,,,,,,,\n")
        
        # Salvataggio del DataFrame in coda al file aperto, rinominando 'annotation' in stringa vuota
        df_final.rename(columns={'annotation': ''}).to_csv(f, index=False)

    print(f"Conversione completata con successo tramite Pandas. File salvato in: {output_filepath}")

# Esecuzione
if __name__ == "__main__":
    convert_to_influx_pandas("input.csv", "output.csv")