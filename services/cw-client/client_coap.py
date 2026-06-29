import asyncio
import json
import logging
import aiohttp
import os
import signal
import struct
import paho.mqtt.client as mqtt_paho
from aiocoap import Context, Message, GET

CONTROLLER_URL = os.getenv("CONTROLLER_URL", "http://localhost:3001/api/data")
STAR_COAP_URI  = os.getenv("STAR_COAP_URI",  "coap://192.168.4.1/telemetry")
STAR_HTTP_BASE = os.getenv("STAR_HTTP_BASE",  "http://192.168.4.1")
MQTT_BROKER    = os.getenv("MQTT_BROKER",    "localhost")
MQTT_PORT      = int(os.getenv("MQTT_PORT",  "1883"))
TIMEOUT_SECONDS = 180.0

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Must match TelemetryPacket.h (little-endian):
# uint32 timestamp, uint32 node_id, float water_temp, float tds_value,
# float soil_moisture, float light_lux, float air_temp, float humidity,
# float pressure, float leaf_temp
_STRUCT_FMT = '<IIffffffff'
_STRUCT_SIZE = struct.calcsize(_STRUCT_FMT)


async def fetch_star_id(session: aiohttp.ClientSession) -> str:
    """Query the Star's /info endpoint to get its star_id."""
    url = f"{STAR_HTTP_BASE}/info"
    for attempt in range(5):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                star_id = str(data["star_id"])
                logger.info(f"Discovered star_id={star_id} from {url}")
                return star_id
        except Exception as e:
            logger.warning(f"Could not reach Star /info (attempt {attempt+1}/5): {e}")
            await asyncio.sleep(5)
    logger.error("Could not discover star_id after 5 attempts, forwarding with empty star_id")
    return ""


def _start_command_listener(star_id: str, loop: asyncio.AbstractEventLoop):
    """Subscribe to MQTT commands for this star, forward to Star HTTP, and ACK back."""
    async def _post_to_star(payload: bytes):
        async with aiohttp.ClientSession() as s:
            try:
                async with s.post(f"{STAR_HTTP_BASE}/command", data=payload,
                                  headers={"Content-Type": "application/json"},
                                  timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        nid = json.loads(payload).get("nid")
                        mqtt_client.publish("greenhouse/acks",
                                            json.dumps({"ack": 1, "nid": nid}))
                        logger.info(f"Command forwarded and ACK published for node {nid}")
                    else:
                        logger.error(f"Star returned HTTP {resp.status}, no ACK sent")
            except Exception as e:
                logger.error(f"Failed to forward command to Star: {e}")

    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            client.subscribe(f"greenhouse/commands/{star_id}")
            logger.info(f"MQTT subscribed to greenhouse/commands/{star_id}")
        else:
            logger.error(f"MQTT connect failed rc={rc}")

    def on_message(client, userdata, msg):
        asyncio.run_coroutine_threadsafe(_post_to_star(msg.payload), loop)

    mqtt_client = mqtt_paho.Client()
    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message
    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        mqtt_client.loop_start()
        logger.info(f"Command listener started ({MQTT_BROKER}:{MQTT_PORT})")
    except Exception as e:
        logger.error(f"Command listener could not connect to MQTT: {e}")


class TelemetryObserver:
    def __init__(self):
        self.context = None
        self.http_session = None
        self.keep_running = True
        self.star_id = ""

    async def _forward(self, payload: dict):
        try:
            async with self.http_session.post(CONTROLLER_URL, json=payload) as resp:
                if resp.status == 200:
                    logger.info(f"Forwarded to controller (node_id={payload.get('node_id')})")
                else:
                    logger.error(f"Controller returned HTTP {resp.status}")
        except Exception as e:
            logger.error(f"Controller unreachable: {e}")

    def _parse(self, data: bytes):
        if not data:
            logger.debug("Empty keep-alive packet, ignoring")
            return

        if len(data) != _STRUCT_SIZE:
            logger.warning(f"Size mismatch: expected {_STRUCT_SIZE}B, got {len(data)}B")
            return

        try:
            fields = struct.unpack(_STRUCT_FMT, data)
        except struct.error as e:
            logger.error(f"Unpack error: {e}")
            return

        timestamp, node_id, water_temp, tds, soil_moisture, light_lux, air_temp, humidity, pressure, leaf_temp = fields

        if timestamp == 0 and node_id == 0:
            logger.info("Star not yet populated with node data, waiting...")
            return

        payload = {
            "star_id":       self.star_id,
            "timestamp":     timestamp,
            "node_id":       node_id,
            "water_temp":    round(water_temp, 2),
            "tds":           round(tds, 2),
            "soil_moisture": round(soil_moisture, 2),
            "light_lux":     round(light_lux, 2),
            "air_temp":      round(air_temp, 2),
            "humidity":      round(humidity, 2),
            "pressure":      round(pressure, 2),
            "leaf_temp":     round(leaf_temp, 2),
        }
        logger.info(f"Decoded: node={node_id} | air={air_temp:.1f}°C | pressure={pressure:.1f}Pa")
        asyncio.create_task(self._forward(payload))

    async def start_observing(self):
        self.context = await Context.create_client_context()
        self.http_session = aiohttp.ClientSession()

        self.star_id = await fetch_star_id(self.http_session)

        if self.star_id:
            _start_command_listener(self.star_id, asyncio.get_event_loop())

        while self.keep_running:
            observation = None
            try:
                request = Message(code=GET, uri=STAR_COAP_URI, observe=0)
                observation = self.context.request(request)
                logger.info(f"Subscribing to {STAR_COAP_URI}...")
                iterator = observation.observation.__aiter__()

                while self.keep_running:
                    response = await asyncio.wait_for(iterator.__anext__(), timeout=TIMEOUT_SECONDS)
                    self._parse(response.payload)

            except asyncio.TimeoutError:
                logger.warning(f"No data for {TIMEOUT_SECONDS}s, reconnecting...")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Connection error ({e}), retrying in 5s...")
                await asyncio.sleep(5)
            finally:
                if observation and not observation.observation.cancelled:
                    observation.observation.cancel()

    async def shutdown(self):
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
