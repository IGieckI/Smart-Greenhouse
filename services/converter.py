import sys
import pandas as pd
from datetime import datetime, timezone
import subprocess
import os
from dotenv import load_dotenv

load_dotenv()

# Utility file to convert a csv estracted by operatorTools/StarInterface.py 
# Data stored in that CSV will be updated direcly into Influx DB
# To dump all data in Influx, you can use ./save_container_status.sh

ORG = os.getenv("ORG", "iot_org")
BUCKET = os.getenv("BUCKET", "sensor_data")
TOKEN = os.getenv("TOKEN")
CONTAINER_NAME = os.getenv("CONTAINER_NAME", "influxdb")

def convert_to_influx_pandas(input_filepath, output_filepath):
    print(f"Converting {input_filepath} to InfluxDB format...")
    
    try:
        df = pd.read_csv(input_filepath)
    except FileNotFoundError:
        print(f"Error: The file {input_filepath} was not found.")
        return False

    if df.empty:
        print("Error: The CSV file is empty.")
        return False

    df_melted = pd.melt(
        df, 
        id_vars=['timestamp', 'node_id'], 
        var_name='_field', 
        value_name='_value'
    )

    df_melted['_time'] = pd.to_datetime(df_melted['timestamp']).dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    df_melted['table'] = df_melted.groupby('_field').ngroup()
    df_melted['result'] = ''
    df_melted['_start'] = "1970-01-01T00:00:00Z"
    df_melted['_stop'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    df_melted['_measurement'] = "sensor_measurements"
    df_melted['id_board'] = df_melted['node_id']
    df_melted['annotation'] = '' 

    final_columns = [
        'annotation', 'result', 'table', '_start', '_stop', 
        '_time', '_value', '_field', '_measurement', 'id_board'
    ]
    df_final = df_melted[final_columns]

    try:
        with open(output_filepath, mode='w', encoding='utf-8') as f:
            f.write("#group,false,false,true,true,false,false,true,true,true\n")
            f.write("#datatype,string,long,dateTime:RFC3339,dateTime:RFC3339,dateTime:RFC3339,double,string,string,string\n")
            f.write("#default,_result,,,,,,,,\n")
            
            df_final.rename(columns={'annotation': ''}).to_csv(f, index=False)
            
        print(f"Conversion successfully completed via Pandas. File saved in: {output_filepath}")
        return True
    except Exception as e:
        print(f"Error during file writing: {e}")
        return False

def import_to_influxdb(csv_filepath):
    print(f"Importing {csv_filepath} into local database...")
    
    if not os.path.exists(csv_filepath):
        print(f"Error: The file '{csv_filepath}' does not exist.")
        return

    if not TOKEN:
        print("Error: InfluxDB TOKEN is missing. Please ensure 'TOKEN' is set in your .env file.")
        return

    command = [
        "docker", "exec", "-i", CONTAINER_NAME,
        "influx", "write",
        "--bucket", BUCKET,
        "--org", ORG,
        "--token", TOKEN,
        "--format", "csv"
    ]

    try:
        with open(csv_filepath, 'rb') as file_input:
            result = subprocess.run(command, stdin=file_input, capture_output=True, text=True)

        if result.returncode == 0:
            print("Import completed! Your InfluxDB now contains the merged data.")
        else:
            print("Error during data import.")
            print(f"Details: {result.stderr}")
            
    except Exception as e:
        print(f"An unexpected error occurred during import: {e}")

if __name__ == "__main__":
    input_file = sys.argv[1] if len(sys.argv) > 1 else "input.csv"
    output_file = "output.csv"

    success = convert_to_influx_pandas(input_file, output_file)
    
    if success:
        import_to_influxdb(output_file)