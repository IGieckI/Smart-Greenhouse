import paho.mqtt.client as mqtt
import requests
import json
import os

BROKER = os.getenv("MQTT_BROKER", "localhost")
PORT = int(os.getenv("MQTT_PORT", 1883))
TOPIC = os.getenv("MQTT_TOPIC", "v3/+/devices/+/up") # Esempio topic TTN
CONTROLLER_URL = os.getenv("CONTROLLER_URL", "http://controller:3001/api/data")

def on_connect(client, userdata, flags, rc):
    print(f"[LW-Client] Connesso al broker MQTT con codice {rc}")
    client.subscribe(TOPIC)

def on_message(client, userdata, msg):
    print(f"[LW-Client] Messaggio ricevuto su {msg.topic}")
    try:
        # Assumiamo che il payload sia nel formato JSON standard del Network Server
        payload = json.loads(msg.payload.decode())
        
        # Estrai i dati utili. (Adatta questa parte in base al JSON del tuo LNS)
        decoded_payload = payload.get("uplink_message", {}).get("decoded_payload", payload)
        
        # Aggiungi un id fittizio se non presente per il PoC
        if "id_board" not in decoded_payload:
            decoded_payload["id_board"] = "heltec_lora_01"

        # Inoltra al controller
        response = requests.post(CONTROLLER_URL, json=decoded_payload)
        print(f"[LW-Client] Inoltrato al controller. Status: {response.status_code}")
    except Exception as e:
        print(f"[LW-Client] Errore nel processamento del messaggio: {e}")

client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message

client.connect(BROKER, PORT, 60)
client.loop_forever()