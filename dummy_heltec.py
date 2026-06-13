import asyncio
import json
import random
from aiocoap import *

# Se stai lanciando questo script dallo stesso PC dove gira Docker Compose,
# localhost va benissimo, perché la porta 5683 UDP è esposta.
TARGET_URI = 'coap://localhost:5683/sensors'

async def simulate_board():
    print("🌿 Avvio Simulatore Heltec Dummy...")
    context = await Context.create_client_context()
    
    while True:
        # Generazione dati dummy realistici
        payload_data = {
            "id_board": "heltec_dummy_test",
            "air_temp": round(random.uniform(20.0, 32.0), 1),
            "air_hum": round(random.uniform(40.0, 80.0), 1),
            "air_press": round(random.uniform(1005, 1025), 0),
            "soil_temp": round(random.uniform(18.0, 24.0), 1),
            "soil_hum": round(random.uniform(30.0, 60.0), 1),
            "tds": round(random.uniform(300, 900), 1),
            "irradiation": round(random.uniform(200, 1000), 0)
        }

        # Convertiamo il dizionario in stringa JSON e poi in Bytes
        payload_bytes = json.dumps(payload_data).encode('utf-8')
        
        # Prepariamo la richiesta CoAP (POST)
        request = Message(code=POST, payload=payload_bytes, uri=TARGET_URI)

        try:
            print(f"\n[INVIA] Spedizione pacchetto: {payload_data}")
            response = await context.request(request).response
            print(f"[RICEVUTA] Risposta dal server CoAP: {response.code} -> {response.payload.decode('utf-8')}")
        except Exception as e:
            print(f"[ERRORE] Impossibile raggiungere il server CoAP: {e}")

        # Attesa di 10 secondi
        await asyncio.sleep(10)

if __name__ == "__main__":
    # Assicurati di aver fatto 'pip install aiocoap' sul tuo pc
    asyncio.run(simulate_board())