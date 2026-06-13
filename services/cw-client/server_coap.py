import asyncio
import logging
import aiohttp
import json
import os
import aiocoap.resource as resource
from aiocoap import Context, Message, CHANGED, BAD_REQUEST, INTERNAL_SERVER_ERROR

CONTROLLER_URL = os.getenv("CONTROLLER_URL", "http://controller:3001/api/data")
logging.basicConfig(level=logging.INFO)

async def forward_to_controller(payload):
    """
    Task in background che inoltra i dati al controller Node.js.
    Questo avviene MENTRE la scheda è già tornata a dormire.
    """
    try:
        # aiohttp è il client HTTP asincrono per eccellenza in Python
        async with aiohttp.ClientSession() as session:
            async with session.post(CONTROLLER_URL, json=payload) as response:
                if response.status == 200:
                    logging.info(f"[CW-Server] Dati salvati con successo dal controller (Board: {payload.get('id_board')})")
                else:
                    # InfluxDB potrebbe essere giù o il formato errato
                    logging.error(f"[CW-Server] Controller ha restituito un errore HTTP {response.status}")
    except Exception as e:
        logging.error(f"[CW-Server] Impossibile contattare il controller: {e}")


class SensorResource(resource.Resource):
    """Risorsa CoAP che riceve i dati via POST/PUT dalle schede."""
    
    async def render_post(self, request):
        try:
            # 1. Decodifica e validazione ultra-veloce
            payload_str = request.payload.decode('utf-8')
            payload = json.loads(payload_str)
            
            if "id_board" not in payload:
                payload["id_board"] = "heltec_coap_01"
                
            logging.info(f"[CW-Server] Ricevuto pacchetto UDP dalla board {payload['id_board']}")
            logging.info(f"{payload}")
            
            # 2. Schedula l'inoltro in background senza aspettarne la fine (Fire and Forget)
            asyncio.create_task(forward_to_controller(payload))
            
            # 3. Rispondi SUBITO alla Heltec con un 200 OK (CHANGED)
            # La scheda riceve questo messaggio in pochi millisecondi e si addormenta.
            return Message(code=CHANGED, payload=b"ACK_OK")
            
        except json.JSONDecodeError:
            logging.error("[CW-Server] Errore: Ricevuto JSON non valido dalla scheda.")
            return Message(code=BAD_REQUEST, payload=b"ERR_JSON")
        except Exception as e:
            logging.error(f"[CW-Server] Errore interno al server CoAP: {e}")
            return Message(code=INTERNAL_SERVER_ERROR, payload=b"ERR_INTERNAL")

    async def render_put(self, request):
        return await self.render_post(request)


async def main():
    root = resource.Site()
    root.add_resource(['sensors'], SensorResource())

    await Context.create_server_context(root, bind=('0.0.0.0', 5683))
    logging.info("[CW-Server] Server CoAP in ascolto sulla porta 5683 UDP (endpoint: /sensors)...")

    await asyncio.get_running_loop().create_future()

if __name__ == "__main__":
    asyncio.run(main())