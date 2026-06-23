import asyncio
import logging
import aiohttp
import os
import signal
import struct
from aiocoap import Context, Message, GET

CONTROLLER_URL = os.getenv("CONTROLLER_URL", "http://controller:3001/api/data")
STAR_COAP_URI = os.getenv("STAR_COAP_URI", "coap://192.168.4.1/telemetry")
TIMEOUT_SECONDS = 180.0  # Tempo massimo di attesa tra un pacchetto e l'altro

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class TelemetryObserver:
    def __init__(self):
        self.context = None
        self.http_session = None
        self.struct_format = '<IIffffffff'
        self.expected_size = struct.calcsize(self.struct_format)
        self.keep_running = True

    async def forward_to_controller(self, payload):
        try:
            async with self.http_session.post(CONTROLLER_URL, json=payload) as response:
                if response.status == 200:
                    logging.info(f"Dati salvati sul controller (Node: {payload.get('node_id')})")
                else:
                    logging.error(f"Errore Controller HTTP {response.status}")
        except Exception as e:
            logging.error(f"Impossibile contattare il controller: {e}")

    def process_payload(self, payload_bytes):
        """Si occupa esclusivamente di parsare il binario ed estrarre il JSON"""
        if not payload_bytes:
            logging.debug("Ricevuto pacchetto di Keep-Alive (Vuoto)")
            return
            
        if len(payload_bytes) == self.expected_size:
            try:
                unpacked = struct.unpack(self.struct_format, payload_bytes)
                
                # Se la ESP32 si è appena accesa e non ha ancora ricevuto dati dal Node
                if unpacked[0] == 0 and unpacked[1] == 0:
                    logging.info("Ricevuta baseline vuota. In attesa dei dati dal Nodo...")
                    return
                
                payload = {
                    "timestamp": unpacked[0],
                    "node_id": unpacked[1],
                    "water_temp": round(unpacked[2], 2),
                    "tds": round(unpacked[3], 2),
                    "soil_moisture": round(unpacked[4], 2),
                    "light_lux": round(unpacked[5], 2),
                    "air_temp": round(unpacked[6], 2),
                    "humidity": round(unpacked[7], 2),
                    "pressure": round(unpacked[8], 2),
                }

                try:
                    payload["leaf_temp"] = round(unpacked[9], 2)
                except IndexError:
                    pass 
                
                logging.info(f"Decodificato: Node {payload['node_id']} | Pres: {payload['pressure']} Pa")
                
                # Schedula l'invio HTTP
                asyncio.create_task(self.forward_to_controller(payload))
                
            except struct.error as e:
                logging.error(f"Errore di decodifica binaria: {e}")
        else:
            logging.warning(f"Dimensione errata! Attesi {self.expected_size}b, ricevuti {len(payload_bytes)}b.")

    async def start_observing(self):
        self.context = await Context.create_client_context()
        self.http_session = aiohttp.ClientSession()

        while self.keep_running:
            observation = None
            try:
                request = Message(code=GET, uri=STAR_COAP_URI, observe=0)
                observation = self.context.request(request)
                
                logging.info(f"Tentativo di Subscribe a {STAR_COAP_URI}...")
                
                # Creiamo l'iteratore manuale per poter inserire il Timeout
                iterator = observation.observation.__aiter__()
                
                while self.keep_running:
                    # Se la Star si spegne o va giù la rete, questo si sblocca dopo 3 minuti e genera TimeoutError
                    response = await asyncio.wait_for(iterator.__anext__(), timeout=TIMEOUT_SECONDS)
                    self.process_payload(response.payload)
        
            except asyncio.TimeoutError:
                logging.warning(f"Timeout! Nessun dato ricevuto da {TIMEOUT_SECONDS}s. Ritento la connessione...")
            except asyncio.CancelledError:
                logging.info("Osservazione annullata dal sistema.")
                break
            except Exception as e:
                logging.error(f"La Star è irragiungibile o c'è un errore di rete ({e}). Ritento tra 5s...")
                await asyncio.sleep(5)
            finally:
                if observation and not observation.observation.cancelled:
                    observation.observation.cancel()

    async def shutdown(self):
        logging.info("Spegnimento del client...")
        self.keep_running = False
            
        if self.http_session:
            await self.http_session.close()
            
        if self.context:
            await self.context.shutdown()

async def main():
    observer = TelemetryObserver()
    loop = asyncio.get_running_loop()

    stop_event = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    observe_task = asyncio.create_task(observer.start_observing())

    await stop_event.wait()

    observe_task.cancel()
    await observer.shutdown()

if __name__ == "__main__":
    asyncio.run(main())


# import asyncio
# import logging
# import aiohttp
# import os
# import signal
# import struct
# from aiocoap import Context, Message, GET

# CONTROLLER_URL = os.getenv("CONTROLLER_URL", "http://controller:3001/api/data")
# STAR_COAP_URI = os.getenv("STAR_COAP_URI", "coap://192.168.4.1/telemetry")
# TIMEOUT_SECONDS = 180.0  # Alziamo il timeout a 3 minuti (i nodi potrebbero avere invii lenti)

# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# class TelemetryObserver:
#     def __init__(self):
#         self.context = None
#         self.http_session = None
#         self.struct_format = '<IIffffffff'
#         self.expected_size = struct.calcsize(self.struct_format)
#         self.keep_running = True

#     async def forward_to_controller(self, payload):
#         try:
#             async with self.http_session.post(CONTROLLER_URL, json=payload) as response:
#                 if response.status == 200:
#                     logging.info(f"Dati salvati sul controller (Node: {payload.get('node_id')})")
#                 else:
#                     logging.error(f"Errore Controller HTTP {response.status}")
#         except Exception as e:
#             logging.error(f"Impossibile contattare il controller: {e}")

#     async def start_observing(self):
#         self.context = await Context.create_client_context()
#         self.http_session = aiohttp.ClientSession()

#         while self.keep_running:
#             observation = None
#             try:
#                 request = Message(code=GET, uri=STAR_COAP_URI, observe=0)
#                 observation = self.context.request(request)
                
#                 logging.info(f"Subscribed (Observe) a {STAR_COAP_URI}. In attesa di dati in tempo reale...")
                
#                 # Iteriamo direttamente sulla risposta
#                 async for response in observation.observation:
                    
#                     payload_bytes = response.payload
                    
#                     # Ignoriamo i pacchetti di solo ACK (vuoti)
#                     if not payload_bytes:
#                         logging.debug("Ricevuto pacchetto di Keep-Alive (Vuoto)")
#                         continue
                        
#                     if len(payload_bytes) == self.expected_size:
#                         try:
#                             unpacked = struct.unpack(self.struct_format, payload_bytes)
                            
#                             # Ignora i pacchetti vuoti/iniziali della ESP se la struct è piena di zeri
#                             if unpacked[0] == 0 and unpacked[1] == 0:
#                                 logging.info("Ricevuta baseline vuota. Aspetto i primi dati veri...")
#                                 continue
                            
#                             payload = {
#                                 "timestamp": unpacked[0],
#                                 "node_id": unpacked[1],
#                                 "water_temp": round(unpacked[2], 2),
#                                 "tds": round(unpacked[3], 2),
#                                 "soil_moisture": round(unpacked[4], 2),
#                                 "light_lux": round(unpacked[5], 2),
#                                 "air_temp": round(unpacked[6], 2),
#                                 "humidity": round(unpacked[7], 2),
#                                 "pressure": round(unpacked[8], 2),
#                             }

#                             try:
#                                 payload["leaf_temp"] = round(unpacked[9], 2)
#                             except IndexError:
#                                 pass 
                            
#                             logging.info(f"Decodificato: Node {payload['node_id']} | Pres: {payload['pressure']} Pa")
                            
#                             asyncio.create_task(self.forward_to_controller(payload))
                            
#                         except struct.error as e:
#                             logging.error(f"Errore di decodifica binaria: {e}")
#                     else:
#                         logging.warning(f"Dimensione errata! Attesi {self.expected_size}b, ricevuti {len(payload_bytes)}b. Raw: {payload_bytes}")
            
#             except asyncio.CancelledError:
#                 logging.info("Osservazione annullata.")
#                 break
                
#             except Exception as e:
#                 logging.error(f"Errore di connessione o Timeout ({e}). Riconnessione tra 5s...")
#                 await asyncio.sleep(5)
                
#             finally:
#                 if observation and not observation.observation.cancelled:
#                     observation.observation.cancel()

#     async def shutdown(self):
#         logging.info("Spegnimento del client...")
#         self.keep_running = False
            
#         if self.http_session:
#             await self.http_session.close()
            
#         if self.context:
#             await self.context.shutdown()

# async def main():
#     observer = TelemetryObserver()
#     loop = asyncio.get_running_loop()

#     stop_event = asyncio.Event()
#     for sig in (signal.SIGINT, signal.SIGTERM):
#         loop.add_signal_handler(sig, stop_event.set)

#     observe_task = asyncio.create_task(observer.start_observing())

#     await stop_event.wait()

#     observe_task.cancel()
#     await observer.shutdown()

# if __name__ == "__main__":
#     asyncio.run(main())

# # import asyncio
# # import logging
# # import aiohttp
# # import os
# # import signal
# # import struct
# # from aiocoap import Context, Message, GET

# # CONTROLLER_URL = os.getenv("CONTROLLER_URL", "http://controller:3001/api/data")
# # STAR_COAP_URI = os.getenv("STAR_COAP_URI", "coap://192.168.4.1/telemetry")
# # # Timeout in secondi: se non arrivano dati per questo tempo, il client si riconnette
# # TIMEOUT_SECONDS = 300.0 

# # logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# # class TelemetryObserver:
# #     def __init__(self):
# #         self.context = None
# #         self.http_session = None
# #         self.struct_format = '<IIffffffff'
# #         self.expected_size = struct.calcsize(self.struct_format)
# #         self.keep_running = True # Flag per controllare il ciclo di vita

# #     async def forward_to_controller(self, payload):
# #         """Inoltra i dati in tempo reale al controller."""
# #         try:
# #             async with self.http_session.post(CONTROLLER_URL, json=payload) as response:
# #                 if response.status == 200:
# #                     logging.info(f"Dati salvati sul controller (Node: {payload.get('node_id')})")
# #                 else:
# #                     logging.error(f"Errore Controller HTTP {response.status}")
# #         except Exception as e:
# #             logging.error(f"Impossibile contattare il controller: {e}")

# #     async def start_observing(self):
# #         """Avvia la connessione e gestisce le auto-riconnessioni."""
# #         self.context = await Context.create_client_context()
# #         self.http_session = aiohttp.ClientSession()

# #         # Ciclo infinito per riprovare la connessione in caso di timeout o errori
# #         while self.keep_running:
# #             observation = None
# #             try:
# #                 request = Message(code=GET, uri=STAR_COAP_URI, observe=0)
# #                 observation = self.context.request(request)
                
# #                 logging.info(f"Subscribed (Observe) a {STAR_COAP_URI}. In attesa di dati in tempo reale...")
                
# #                 # Estraiamo l'iteratore asincrono per poter usare asyncio.wait_for
# #                 iterator = observation.observation.__aiter__()

# #                 while self.keep_running:
# #                     # Aspettiamo il pacchetto con un Timeout. 
# #                     # Se la ESP32 tace per più di 60 secondi, lancia un TimeoutError
# #                     response = await asyncio.wait_for(iterator.__anext__(), timeout=TIMEOUT_SECONDS)
                    
# #                     payload_bytes = response.payload
                    
# #                     if len(payload_bytes) == self.expected_size:
# #                         try:
# #                             unpacked = struct.unpack(self.struct_format, payload_bytes)
                            
# #                             payload = {
# #                                 "timestamp": unpacked[0],
# #                                 "node_id": unpacked[1],
# #                                 "water_temp": round(unpacked[2], 2),
# #                                 "tds": round(unpacked[3], 2),
# #                                 "soil_moisture": round(unpacked[4], 2),
# #                                 "light_lux": round(unpacked[5], 2),
# #                                 "air_temp": round(unpacked[6], 2),
# #                                 "humidity": round(unpacked[7], 2),
# #                                 "pressure": round(unpacked[8], 2),
# #                             }

# #                             try:
# #                                 payload["leaf_temp"] = round(unpacked[9], 2)
# #                             except IndexError:
# #                                 pass 
                            
# #                             logging.info(f"Decodificato: Node {payload['node_id']} | Pres: {payload['pressure']} Pa")
                            
# #                             asyncio.create_task(self.forward_to_controller(payload))
                            
# #                         except struct.error as e:
# #                             logging.error(f"Errore di decodifica binaria: {e}")
# #                     else:
# #                         logging.warning(f"Dimensione errata! Attesi {self.expected_size}b, ricevuti {len(payload_bytes)}b.")
            
# #             except asyncio.TimeoutError:
# #                 logging.warning(f"Timeout! Nessun dato ricevuto da {TIMEOUT_SECONDS}s. La Star potrebbe essersi riavviata.")
# #                 # Non facciamo nulla di speciale: il ciclo `while` ricomincerà e farà un nuovo Subscribe
            
# #             except asyncio.CancelledError:
# #                 logging.info("Osservazione annullata dal sistema (Spegnimento in corso...).")
# #                 break
                
# #             except Exception as e:
# #                 logging.error(f"Errore inaspettato durante l'osservazione: {e}")
# #                 await asyncio.sleep(5) # Pausa di sicurezza prima di riprovare
                
# #             finally:
# #                 # Pulizia dell'osservazione precedente prima di riavviarne una nuova
# #                 if observation and not observation.observation.cancelled:
# #                     observation.observation.cancel()

# #     async def shutdown(self):
# #         """Chiusura pulita."""
# #         logging.info("Spegnimento del client... Pulizia risorse.")
# #         self.keep_running = False
            
# #         if self.http_session:
# #             await self.http_session.close()
            
# #         if self.context:
# #             await self.context.shutdown()

# # async def main():
# #     observer = TelemetryObserver()
# #     loop = asyncio.get_running_loop()

# #     stop_event = asyncio.Event()
# #     for sig in (signal.SIGINT, signal.SIGTERM):
# #         loop.add_signal_handler(sig, stop_event.set)

# #     observe_task = asyncio.create_task(observer.start_observing())

# #     await stop_event.wait()

# #     observe_task.cancel()
# #     await observer.shutdown()

# # if __name__ == "__main__":
# #     asyncio.run(main())


# # # import asyncio
# # # import logging
# # # import aiohttp
# # # import os
# # # import signal
# # # import struct
# # # from aiocoap import Context, Message, GET

# # # CONTROLLER_URL = os.getenv("CONTROLLER_URL", "http://controller:3001/api/data")
# # # STAR_COAP_URI = os.getenv("STAR_COAP_URI", "coap://192.168.4.1/telemetry")

# # # logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# # # class TelemetryObserver:
# # #     def __init__(self):
# # #         self.context = None
# # #         self.observation = None
# # #         self.http_session = None
        
# # #         # DEFINIZIONE DELLA STRUCT C
# # #         # '<' = Little-Endian (Standard ESP32)
# # #         # 'I' = unsigned int (4 byte) per timestamp e node_id
# # #         # 'f' = float (4 byte) per le 8 misurazioni successive
# # #         self.struct_format = '<IIffffffff'
# # #         self.expected_size = struct.calcsize(self.struct_format)

# # #     async def forward_to_controller(self, payload):
# # #         """Inoltra i dati in tempo reale al controller."""
# # #         try:
# # #             async with self.http_session.post(CONTROLLER_URL, json=payload) as response:
# # #                 if response.status == 200:
# # #                     logging.info(f"Dati salvati sul controller (Node: {payload.get('node_id')})")
# # #                 else:
# # #                     logging.error(f"Errore Controller HTTP {response.status}")
# # #         except Exception as e:
# # #             logging.error(f"Impossibile contattare il controller: {e}")

# # #     async def start_observing(self):
# # #         """Inizia il SUBSCRIBE (Observe) verso la ESP32 Star."""
# # #         self.context = await Context.create_client_context()
# # #         self.http_session = aiohttp.ClientSession()

# # #         request = Message(code=GET, uri=STAR_COAP_URI, observe=0)
# # #         self.observation = self.context.request(request)

# # #         logging.info(f"Subscribed (Observe) a {STAR_COAP_URI}. In attesa di dati in tempo reale...")

# # #         try:
# # #             async for response in self.observation.observation:
# # #                 payload_bytes = response.payload
                
# # #                 # Controllo di sicurezza sulle dimensioni del pacchetto
# # #                 if len(payload_bytes) == self.expected_size:
# # #                     try:
# # #                         # Unpack della struct binaria
# # #                         unpacked = struct.unpack(self.struct_format, payload_bytes)
                        
# # #                         # Ricostruiamo il dizionario per il Node.js
# # #                         payload = {
# # #                             "timestamp": unpacked[0],
# # #                             "node_id": unpacked[1],
# # #                             "water_temp": round(unpacked[2], 2),
# # #                             "tds": round(unpacked[3], 2),
# # #                             "soil_moisture": round(unpacked[4], 2),
# # #                             "light_lux": round(unpacked[5], 2),
# # #                             "air_temp": round(unpacked[6], 2),
# # #                             "humidity": round(unpacked[7], 2),
# # #                             "pressure": round(unpacked[8], 2),
# # #                         }

# # #                         try:
# # #                             payload["leaf_temp"] = round(unpacked[9], 2)
# # #                         except:
# # #                             logging.info(f"leaf_temp mancante nel payload")    
                        
# # #                         logging.info(f"Ricevuto binario e decodificato: {payload}")
                        
# # #                         # Fire and forget verso il Node.js
# # #                         asyncio.create_task(self.forward_to_controller(payload))
                        
# # #                     except struct.error as e:
# # #                         logging.error(f"Errore di decodifica binaria: {e}")
# # #                 else:
# # #                     logging.warning(f"Dimensione pacchetto errata! Attesi {self.expected_size} bytes, ricevuti {len(payload_bytes)} bytes.")
                    
# # #         except asyncio.CancelledError:
# # #             logging.info("Osservazione annullata (Desubscribe in corso...)")

# # #     async def shutdown(self):
# # #         """Desubscribe e chiusura pulita delle connessioni."""
# # #         logging.info("Spegnimento del client... Invio DESUBSCRIBE alla Star.")
        
# # #         if self.observation and not self.observation.observation.cancelled:
# # #             self.observation.observation.cancel() 
            
# # #         if self.http_session:
# # #             await self.http_session.close()
            
# # #         if self.context:
# # #             await self.context.shutdown()

# # # async def main():
# # #     observer = TelemetryObserver()
# # #     loop = asyncio.get_running_loop()

# # #     # Gestione spegnimento pulito per Docker
# # #     stop_event = asyncio.Event()
# # #     for sig in (signal.SIGINT, signal.SIGTERM):
# # #         loop.add_signal_handler(sig, stop_event.set)

# # #     observe_task = asyncio.create_task(observer.start_observing())

# # #     await stop_event.wait()

# # #     observe_task.cancel()
# # #     await observer.shutdown()

# # # if __name__ == "__main__":
# # #     asyncio.run(main())


# # # # import asyncio
# # # # import logging
# # # # import aiohttp
# # # # import json
# # # # import os
# # # # import signal
# # # # from aiocoap import Context, Message, GET

# # # # CONTROLLER_URL = os.getenv("CONTROLLER_URL", "http://controller:3001/api/data")
# # # # STAR_COAP_URI = os.getenv("STAR_COAP_URI", "coap://192.168.4.1/telemetry")

# # # # logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# # # # class TelemetryObserver:
# # # #     def __init__(self):
# # # #         self.context = None
# # # #         self.observation = None
# # # #         self.http_session = None

# # # #     async def forward_to_controller(self, payload):
# # # #         """Inoltra i dati in tempo reale al controller."""
# # # #         try:
# # # #             async with self.http_session.post(CONTROLLER_URL, json=payload) as response:
# # # #                 if response.status == 200:
# # # #                     logging.info(f"Dati salvati sul controller: {payload.get('node_id')}")
# # # #                 else:
# # # #                     logging.error(f"Errore Controller HTTP {response.status}")
# # # #         except Exception as e:
# # # #             logging.error(f"Impossibile contattare il controller: {e}")

# # # #     async def start_observing(self):
# # # #         """Inizia il SUBSCRIBE (Observe) verso la ESP32 Star."""
# # # #         self.context = await Context.create_client_context()
# # # #         self.http_session = aiohttp.ClientSession()

# # # #         request = Message(code=GET, uri=STAR_COAP_URI, observe=0)
# # # #         self.observation = self.context.request(request)

# # # #         logging.info(f"Subscribed (Observe) a {STAR_COAP_URI}. In attesa di dati in tempo reale...")

# # # #         try:
# # # #             # Questo è un generatore asincrono: itererà ogni volta che la ESP32 
# # # #             # chiama coap_resource_notify_observers()
# # # #             async for response in self.observation.observation:
# # # #                 try:
# # # #                     # Assumendo che la ESP32 mandi un JSON. Se hai mandato i byte puri 
# # # #                     # dalla struct C, dovrai usare la libreria struct di Python per decodificare.
# # # #                     payload_str = response.payload.decode('utf-8')
# # # #                     payload = json.loads(payload_str)
                    
# # # #                     logging.info(f"Nuovo pacchetto in real-time ricevuto: {payload}")
                    
# # # #                     # # Fire and forget verso il Node.js
# # # #                     # asyncio.create_task(self.forward_to_controller(payload))
                    
# # # #                 except json.JSONDecodeError:
# # # #                     logging.warning(f"Payload non JSON ricevuto: {response.payload}")
                    
# # # #         except asyncio.CancelledError:
# # # #             logging.info("Osservazione annullata (Desubscribe in corso...)")

# # # #     async def shutdown(self):
# # # #         """Desubscribe e chiusura pulita delle connessioni."""
# # # #         logging.info("Spegnimento del client... Invio DESUBSCRIBE alla Star.")
        
# # # #         if self.observation and not self.observation.observation.cancelled:
# # # #             # Questo notifica al server CoAP di rimuovere l'Observer
# # # #             self.observation.observation.cancel() 
            
# # # #         if self.http_session:
# # # #             await self.http_session.close()
            
# # # #         if self.context:
# # # #             await self.context.shutdown()

# # # # async def main():
# # # #     observer = TelemetryObserver()
# # # #     loop = asyncio.get_running_loop()

# # # #     # Gestione spegnimento pulito per Docker (SIGINT/SIGTERM)
# # # #     stop_event = asyncio.Event()
# # # #     for sig in (signal.SIGINT, signal.SIGTERM):
# # # #         loop.add_signal_handler(sig, stop_event.set)

# # # #     # Avvia l'osservazione in background
# # # #     observe_task = asyncio.create_task(observer.start_observing())

# # # #     # Attendi il segnale di spegnimento da Docker
# # # #     await stop_event.wait()

# # # #     # Esegui il desubscribe e chiudi
# # # #     observe_task.cancel()
# # # #     await observer.shutdown()

# # # # if __name__ == "__main__":
# # # #     asyncio.run(main())

# # # # # import asyncio
# # # # # import logging
# # # # # import aiohttp
# # # # # import json
# # # # # import os
# # # # # import signal
# # # # # from aiocoap import Context, Message, GET

# # # # # # Configurazione
# # # # # CONTROLLER_URL = os.getenv("CONTROLLER_URL", "http://controller:3001/api/data")
# # # # # STAR_GATEWAY_IP = os.getenv("STAR_GATEWAY_IP", "192.168.4.1") # IP della ESP32 in modalità AP
# # # # # COAP_URI = f"coap://{STAR_GATEWAY_IP}/telemetry/latest"

# # # # # logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# # # # # async def forward_to_controller(session, payload):
# # # # #     """Inoltra i dati in tempo reale al controller Node.js"""
# # # # #     try:
# # # # #         async with session.post(CONTROLLER_URL, json=payload) as response:
# # # # #             if response.status == 200:
# # # # #                 logging.info(f"Dati salvati (Board: {payload.get('node_id', 'Sconosciuto')})")
# # # # #             else:
# # # # #                 logging.error(f"Errore controller HTTP {response.status}")
# # # # #     except Exception as e:
# # # # #         logging.error(f"Impossibile contattare il controller: {e}")

# # # # # async def observe_telemetry():
# # # # #     """Si connette al gateway ESP32 usando CoAP Observe (Subscribe)"""
# # # # #     protocol = await Context.create_client_context()
    
# # # # #     # Prepariamo la richiesta GET con il flag per l'osservazione
# # # # #     request = Message(code=GET, uri=COAP_URI)
# # # # #     request.opt.observe = 0 # 0 significa "Subscribe" in CoAP
    
# # # # #     requester = protocol.request(request)
# # # # #     logging.info(f"Subscribed a {COAP_URI}. In attesa di dati in tempo reale...")
    
# # # # #     async with aiohttp.ClientSession() as http_session:
# # # # #         try:
# # # # #             # Questo ciclo asincrono "scatta" ogni volta che la ESP32 invia una notifica
# # # # #             async for response in requester.observation:
# # # # #                 try:
# # # # #                     payload_str = response.payload.decode('utf-8')
# # # # #                     payload = json.loads(payload_str)
# # # # #                     logging.info(f"Nuovo pacchetto ricevuto via CoAP Observe: {payload}")
                    
# # # # #                     # Fire and forget verso il controller Node.js
# # # # #                     asyncio.create_task(forward_to_controller(http_session, payload))
                    
# # # # #                 except json.JSONDecodeError:
# # # # #                     logging.error("Ricevuto JSON non valido dal Gateway")
                    
# # # # #         except asyncio.CancelledError:
# # # # #             logging.info("Osservazione interrotta (Unsubscribe in corso...)")
# # # # #             # In aiocoap, annullare l'osservazione o chiudere il context 
# # # # #             # dice implicitamente al server di fare unsubscribe.
# # # # #             if not requester.observation.cancelled:
# # # # #                 requester.observation.cancel()

# # # # # async def main():
# # # # #     loop = asyncio.get_running_loop()
    
# # # # #     # Task principale
# # # # #     observe_task = asyncio.create_task(observe_telemetry())

# # # # #     # Gestione spegnimento grazioso (Docker stop / Ctrl+C)
# # # # #     def shutdown_handler():
# # # # #         logging.info("Ricevuto segnale di spegnimento. Chiusura connessioni...")
# # # # #         observe_task.cancel()

# # # # #     for sig in (signal.SIGINT, signal.SIGTERM):
# # # # #         loop.add_signal_handler(sig, shutdown_handler)

# # # # #     try:
# # # # #         await observe_task
# # # # #     except asyncio.CancelledError:
# # # # #         logging.info("Servizio CoAP Client arrestato correttamente.")

# # # # # if __name__ == "__main__":
# # # # #     asyncio.run(main())

# # # # # # import asyncio
# # # # # # import logging
# # # # # # import aiohttp
# # # # # # import json
# # # # # # import os
# # # # # # import aiocoap.resource as resource
# # # # # # from aiocoap import Context, Message, CHANGED, BAD_REQUEST, INTERNAL_SERVER_ERROR

# # # # # # CONTROLLER_URL = os.getenv("CONTROLLER_URL", "http://controller:3001/api/data")
# # # # # # logging.basicConfig(level=logging.INFO)

# # # # # # async def forward_to_controller(payload):
# # # # # #     """
# # # # # #     Task in background che inoltra i dati al controller Node.js.
# # # # # #     Questo avviene MENTRE la scheda è già tornata a dormire.
# # # # # #     """
# # # # # #     try:
# # # # # #         # aiohttp è il client HTTP asincrono per eccellenza in Python
# # # # # #         async with aiohttp.ClientSession() as session:
# # # # # #             async with session.post(CONTROLLER_URL, json=payload) as response:
# # # # # #                 if response.status == 200:
# # # # # #                     logging.info(f"[CW-Server] Dati salvati con successo dal controller (Board: {payload.get('id_board')})")
# # # # # #                 else:
# # # # # #                     # InfluxDB potrebbe essere giù o il formato errato
# # # # # #                     logging.error(f"[CW-Server] Controller ha restituito un errore HTTP {response.status}")
# # # # # #     except Exception as e:
# # # # # #         logging.error(f"[CW-Server] Impossibile contattare il controller: {e}")


# # # # # # class SensorResource(resource.Resource):
# # # # # #     """Risorsa CoAP che riceve i dati via POST/PUT dalle schede."""
    
# # # # # #     async def render_post(self, request):
# # # # # #         try:
# # # # # #             # 1. Decodifica e validazione ultra-veloce
# # # # # #             payload_str = request.payload.decode('utf-8')
# # # # # #             payload = json.loads(payload_str)
            
# # # # # #             if "id_board" not in payload:
# # # # # #                 payload["id_board"] = "heltec_coap_01"
                
# # # # # #             logging.info(f"[CW-Server] Ricevuto pacchetto UDP dalla board {payload['id_board']}")
# # # # # #             logging.info(f"{payload}")
            
# # # # # #             # 2. Schedula l'inoltro in background senza aspettarne la fine (Fire and Forget)
# # # # # #             asyncio.create_task(forward_to_controller(payload))
            
# # # # # #             # 3. Rispondi SUBITO alla Heltec con un 200 OK (CHANGED)
# # # # # #             # La scheda riceve questo messaggio in pochi millisecondi e si addormenta.
# # # # # #             return Message(code=CHANGED, payload=b"ACK_OK")
            
# # # # # #         except json.JSONDecodeError:
# # # # # #             logging.error("[CW-Server] Errore: Ricevuto JSON non valido dalla scheda.")
# # # # # #             return Message(code=BAD_REQUEST, payload=b"ERR_JSON")
# # # # # #         except Exception as e:
# # # # # #             logging.error(f"[CW-Server] Errore interno al server CoAP: {e}")
# # # # # #             return Message(code=INTERNAL_SERVER_ERROR, payload=b"ERR_INTERNAL")

# # # # # #     async def render_put(self, request):
# # # # # #         return await self.render_post(request)


# # # # # # async def main():
# # # # # #     root = resource.Site()
# # # # # #     root.add_resource(['sensors'], SensorResource())

# # # # # #     await Context.create_server_context(root, bind=('0.0.0.0', 5683))
# # # # # #     logging.info("[CW-Server] Server CoAP in ascolto sulla porta 5683 UDP (endpoint: /sensors)...")

# # # # # #     await asyncio.get_running_loop().create_future()

# # # # # # if __name__ == "__main__":
# # # # # #     asyncio.run(main())