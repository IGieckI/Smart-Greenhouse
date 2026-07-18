import os

##### Config variables. Respectevely:
### DATABASE variables
### BOARDS CONFIGURATION
### ENVIRONMENT METADATA (to eventually enrich data)
#in case True:
# 0 -> Unstable / Outdoor-like environment
# 1 -> Stable / Indoor environment
### DATA FREQUENCY 
### Horizon in DAYS for environmental indipendent forecaster
### PREPROCESSING & INTERPOLATION
### ANOMALY DETECTION variables
### Frequencies used as default to populate clean and trained models on startup

INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
INFLUX_ORG = os.getenv("INFLUX_ORG", "iot_org")

BUCKET_RAW = "sensor_data"
BUCKET_CAVEAUX = "caveaux_leaf_temp"
BUCKET_CLEAN_PREFIX = "sensor_data_clean_" 
BASE_MODEL_DIR = "/app/shared_core/models"




BOARD_324 = "3750846324"
BOARD_944 = "3750866944"
DEFAULT_BOARD_ID = BOARD_324
ACTIVE_BOARDS = [BOARD_324, BOARD_944]

TRAIN_SPLIT_PERCENTAGE = 0.90



USE_INDOOR_FEATURE = False

BOARD_ENV_MAP = {
    BOARD_324: 0, 
    BOARD_944: 1 
}




TARGET_FREQ_MINUTES = 6 
SYNC_LOOKBACK_DAYS = "-30d"
INFERENCE_LOOKBACK_DAYS = "-7d"




ENV_ARIMA_TRAIN_DAYS = 14




DEFAULT_LAGS = 6
INTERPOLATION_WIN_BEFORE = 5
INTERPOLATION_WIN_AFTER = 2
INTERPOLATION_SIGMA_MIN = 15.0
LEAF_MAX_GAP_MINUTES = 12
MAX_INTERPOLATION_GAP_MINUTES = 42 



MIN_VALID_WATER_TEMP = 10.0
TDS_ROLLING_WINDOW = 10
TDS_SPIKE_THRESHOLD = 1.3





DEFAULT_FREQS = [6]





def get_virtual_ratio(freq_minutes: int) -> int:
    """
        Calculates the jump ratio to align data to the target frequency.
        Usefull to train model like 
            "have data every 6min but forecast every 30min, to generate in this way 30/6=5 virtual day)
    """
    return max(1, int(TARGET_FREQ_MINUTES / freq_minutes))

def get_min_history_records(freq_minutes: int) -> int:
    """
        Calculates the minimum required historical records based on frequency.
    """
    max_possible_lags = 15
    return (max_possible_lags * get_virtual_ratio(freq_minutes)) + 2

def get_fetch_limits(freq_minutes: int):
    """
        Returns a tuple: (FETCH_LIMIT_LATEST, FETCH_LIMIT_MANUAL).
    """
    min_rec = get_min_history_records(freq_minutes)
    return min_rec + 20, min_rec + 19
