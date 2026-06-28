import os
import sys

sys.path.append('/app')
from shared_core.data_sync import sync_clean_bucket
from shared_core.config import DEFAULT_FREQS


if __name__ == "__main__":
    INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
    INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
    INFLUX_ORG = os.getenv("INFLUX_ORG", "iot_org")
    

    
    print("[Cleaner] Avvio sincronizzazione batch pre-training multiplo...")
    
    for freq in DEFAULT_FREQS:
        print(f"\n--- Sincronizzazione Bucket per {freq} min ---")
        sync_clean_bucket(INFLUX_URL, INFLUX_TOKEN, INFLUX_ORG, freq_minutes=freq)
        
    print("\n[Cleaner] Sincronizzazione completata su tutte le scale temporali.")