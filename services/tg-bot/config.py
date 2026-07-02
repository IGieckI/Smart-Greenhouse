import os
import logging
from zoneinfo import ZoneInfo

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger("GJGreenHouse")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
INFERENCE_URL = os.getenv("INFERENCE_URL", "http://ml-inference:8000")
TRAINER_URL = os.getenv("TRAINER_URL", "http://ml-trainer:8001")
CONTROLLER_URL = os.getenv("CONTROLLER_URL", "http://controller:3001")

INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
INFLUX_ORG = os.getenv("INFLUX_ORG", "iot_org")
BUCKET = "sensor_data"

BOARD_MAP = {"1": "3750846324", "2": "3750866944"}
REVERSE_BOARD_MAP = {v: f"Board {k}" for k, v in BOARD_MAP.items()}
TZ_ROME = ZoneInfo("Europe/Rome")


AWAIT_WHATIF_MODE, AWAIT_WHATIF_TASK, AWAIT_WHATIF_BOARD, AWAIT_WHATIF_VALUES = range(4)