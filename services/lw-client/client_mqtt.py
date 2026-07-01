import paho.mqtt.client as mqtt
import requests
import json
import os
import logging

BROKER = os.getenv("MQTT_BROKER", "mosquitto")
PORT = int(os.getenv("MQTT_PORT", 1883))
TOPIC = os.getenv("MQTT_TOPIC", "greenhouse/telemetry/live")
CONTROLLER_URL = os.getenv("CONTROLLER_URL", "http://controller:3001/api/data")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

_KEY_MAP = {
    "sid": "star_id",
    "ts":  "timestamp",
    "id":  "node_id",
    "p":   "pressure",
    "wt":  "water_temp",
    "lux": "light_lux",
    "tds": "tds",
    "sm":  "soil_moisture",
    "at":  "air_temp",
    "h":   "humidity",
    "lt":  "leaf_temp",
}

def _normalize(raw: dict) -> dict:
    return {_KEY_MAP.get(k, k): v for k, v in raw.items()}

_subscribed_stars = set()

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.info(f"Connected to MQTT broker at {BROKER}, subscribing to '{TOPIC}'")
        client.subscribe(TOPIC)
    else:
        logger.error(f"MQTT connection refused, rc={rc}")

def on_disconnect(client, userdata, rc):
    if rc != 0:
        logger.warning(f"Unexpected disconnect (rc={rc}), will auto-reconnect")

def on_message(client, userdata, msg):
    if msg.topic.startswith("greenhouse/commands/"):
        client.publish("greenhouse/gateway/commands", msg.payload)
        logger.info(f"Forwarded command to Gateway: {msg.payload.decode()}")
        return

    logger.info(f"Message on '{msg.topic}'")
    try:
        raw = json.loads(msg.payload.decode())
        payload = _normalize(raw)

        star_id = payload.get("star_id")
        if star_id and star_id not in _subscribed_stars:
            _subscribed_stars.add(star_id)
            client.subscribe(f"greenhouse/commands/{star_id}")
            logger.info(f"Subscribed to commands for star {star_id}")

        response = requests.post(CONTROLLER_URL, json=payload, timeout=5)
        logger.info(f"Forwarded to controller, status={response.status_code}")
    except json.JSONDecodeError:
        logger.error(f"Invalid JSON payload: {msg.payload!r}")
    except requests.RequestException as e:
        logger.error(f"Controller unreachable: {e}")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")


client = mqtt.Client()
client.on_connect = on_connect
client.on_disconnect = on_disconnect
client.on_message = on_message
client.reconnect_delay_set(min_delay=1, max_delay=30)

client.connect(BROKER, PORT, keepalive=60)
client.loop_forever()
