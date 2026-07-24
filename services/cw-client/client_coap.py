import os

import asyncio
import json
import logging
import time
import aiohttp
import signal
import struct
import paho.mqtt.client as mqtt_paho
from aiocoap import Context, Message, GET, POST

CONTROLLER_URL = os.getenv("CONTROLLER_URL", "http://localhost:3001/api/data")
STAR_COAP_BASE = os.getenv("STAR_COAP_BASE", "coap://192.168.4.1")
STAR_COAP_URI  = os.getenv("STAR_COAP_URI",  f"{STAR_COAP_BASE}/telemetry")
MQTT_BROKER    = os.getenv("MQTT_BROKER",    "localhost")
MQTT_PORT      = int(os.getenv("MQTT_PORT",  "1883"))

# CoAP star reconnection timeouts (seconds)
INIT_TIMEOUT        = 5.0
SUBSCRIBE_TIMEOUT   = 5.0
STREAM_SOFT_TIMEOUT = 300.0
HEARTBEAT_TIMEOUT   = 5.0
INIT_RETRY_DELAY    = 5.0

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)





# Must match TelemetryPacket.h
_STRUCT_FMT = '<IIffffffff'
_STRUCT_SIZE = struct.calcsize(_STRUCT_FMT)


class TelemetryObserver:
    def __init__(self):
        self.context = None
        self.http_session = None
        self.mqtt_client = None
        self.keep_running = True
        self.star_id = ""

    async def _rebuild_context(self):
        """
        Create a fresh CoAP context and atomically swap it in, then shut the old one
        down (avoid a network swap problem)
        """
        new_ctx = await Context.create_client_context()
        old_ctx, self.context = self.context, new_ctx
        if old_ctx is not None:
            try:
                await old_ctx.shutdown()
            except Exception:
                pass

    async def _init_once(self) -> bool:
        """
        Rebuild the context, then GET /info?ts= to discover the star_id and sync the clock.
        """
        try:
            await self._rebuild_context()
            request = Message(code=GET, uri=f"{STAR_COAP_BASE}/info?ts={int(time.time())}")
            response = await asyncio.wait_for(self.context.request(request).response,
                                              timeout=INIT_TIMEOUT)
            payload_str = response.payload.decode('utf-8').strip()
            if payload_str:
                data = json.loads(payload_str)
                if "star_id" in data:
                    self.star_id = str(data["star_id"])
                    logger.info(f"INIT ok: star_id={self.star_id} (clock synced)")
                    return True
            logger.warning("Star /info returned no star_id, retrying INIT...")
        except asyncio.TimeoutError:
            logger.warning(f"INIT timed out (>{INIT_TIMEOUT}s), retrying...")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"INIT failed ({e}), retrying...")
        return False

    async def _heartbeat(self) -> bool:
        """
        Plain GET /info on the current context. It does NOT rebuild the context or disturb the 
        active observation, but confirms the Star link availability (check for disconnection).
        """
        try:
            request = Message(code=GET, uri=f"{STAR_COAP_BASE}/info")
            await asyncio.wait_for(self.context.request(request).response,
                                   timeout=HEARTBEAT_TIMEOUT)
            logger.debug("Heartbeat OK, observation still alive")
            return True
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Heartbeat probe failed: {e}")
            return False

    def _start_command_listener(self, loop: asyncio.AbstractEventLoop):
        """
        Subscribe to MQTT commands, forward each to the Star over CoAP, and ACK back
        """
        async def _post_to_star(payload: bytes):
            """
            Forward the command payload to the Star's /command CoAP resource and publish an ACK to MQTT
            """
            try:
                request = Message(code=POST, uri=f"{STAR_COAP_BASE}/command", payload=payload)
                response = await asyncio.wait_for(self.context.request(request).response, timeout=20.0)
                if response.code.is_successful():
                    nid = json.loads(payload).get("nid")
                    self.mqtt_client.publish("greenhouse/acks",
                                             json.dumps({"ack": 1, "nid": nid}))
                    logger.info(f"Command forwarded and ACK published for node {nid}")
                else:
                    logger.error(f"Star returned CoAP {response.code}, no ACK sent")
            except asyncio.TimeoutError:
                logger.error("Command to Star timed out")
            except Exception as e:
                logger.error(f"Failed to forward command to Star: {e}")

        def on_connect(client, userdata, flags, rc):
            """
            Callback for MQTT connection. Subscribes to the command topic for this star
            """
            if rc == 0:
                topic = f"greenhouse/commands/{self.star_id}"
                client.subscribe(topic)
                logger.info(f"MQTT subscribed to {topic}")
            else:
                logger.error(f"MQTT connect failed rc={rc}")

        def on_message(client, userdata, msg):
            """
            Callback for incoming MQTT messages. Forwards the payload to the Star over CoAP
            """
            asyncio.run_coroutine_threadsafe(_post_to_star(msg.payload), loop)

        self.mqtt_client = mqtt_paho.Client()
        self.mqtt_client.on_connect = on_connect
        self.mqtt_client.on_message = on_message
        try:
            self.mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
            self.mqtt_client.loop_start()
            logger.info(f"Command listener started ({MQTT_BROKER}:{MQTT_PORT})")
        except Exception as e:
            logger.error(f"Command listener could not connect to MQTT: {e}")

    async def _forward(self, payload: dict):
        """
        Forward the decoded telemetry payload to the controller via HTTP POST
        """
        try:
            async with self.http_session.post(CONTROLLER_URL, json=payload) as resp:
                if resp.status == 200:
                    logger.info(f"Forwarded to controller (node_id={payload.get('node_id')})")
                else:
                    logger.error(f"Controller returned HTTP {resp.status}")
        except Exception as e:
            logger.error(f"Controller unreachable: {e}")

    def _parse(self, data: bytes):
        """
        Parse the incoming telemetry data and forward it to the controller
        """
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

        if (timestamp == 0) and (node_id == 0):
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
        """
        Drive the CoAP connection as a small state machine:

        INIT      -> rebuild context, GET /info?ts= (star_id + clock sync)
        SUBSCRIBE -> GET /telemetry observe, expect the immediate cached reply
        STREAM    -> pull notifications; telemetry silence is normal, so a soft
                        timeout runs a /info heartbeat instead of reconnecting
        """
        self.http_session = aiohttp.ClientSession()
        command_listener_started = False

        while self.keep_running:
            if not await self._init_once():
                await asyncio.sleep(INIT_RETRY_DELAY)
                continue

            if not command_listener_started:
                self._start_command_listener(asyncio.get_event_loop())
                command_listener_started = True

            try:
                await self._stream()
            except asyncio.CancelledError:
                break

    async def _stream(self):
        """
        SUBSCRIBE to /telemetry, then STREAM notifications. Returns when connectivity is
        actually lost so the caller re-runs INIT.
        """
        pr = None
        try:
            request = Message(code=GET, uri=STAR_COAP_URI, observe=0)
            pr = self.context.request(request)
            logger.info(f"Subscribing to {STAR_COAP_URI}...")

            response = await asyncio.wait_for(pr.response, timeout=SUBSCRIBE_TIMEOUT)
            self._parse(response.payload)

            iterator = pr.observation.__aiter__()
            while self.keep_running:
                try:
                    response = await asyncio.wait_for(iterator.__anext__(),
                                                      timeout=STREAM_SOFT_TIMEOUT)
                    self._parse(response.payload)
                except asyncio.TimeoutError:
                    if await self._heartbeat():
                        continue
                    logger.warning("Heartbeat failed, reconnecting...")
                    return
        except asyncio.TimeoutError:
            logger.warning(f"No reply to subscribe within {SUBSCRIBE_TIMEOUT}s, reconnecting...")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Observe error ({e}), reconnecting...")
        finally:
            if pr is not None and getattr(pr, 'observation', None) and not pr.observation.cancelled:
                pr.observation.cancel()

    async def shutdown(self):
        """
        Gracefully shutdown the observer, stopping CoAP observation, MQTT, and HTTP session
        """
        self.keep_running = False
        if self.mqtt_client:
            self.mqtt_client.loop_stop()
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
