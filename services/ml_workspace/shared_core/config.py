import os

# ==========================================
# DATABASE & PATHS
# ==========================================
INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
INFLUX_ORG = os.getenv("INFLUX_ORG", "iot_org")

BUCKET_RAW = "sensor_data"
BUCKET_CLEAN = "sensor_data_clean"
BASE_MODEL_DIR = "/app/shared_core/models"

# ==========================================
# BOARDS CONFIGURATION
# ==========================================
BOARD_324 = "3750846324"  # Ex Board di Training
BOARD_944 = "3750866944"  # Ex Board di Test

# Ripristinato per compatibilità con api.py
DEFAULT_BOARD_ID = BOARD_324 

# Ora addestriamo e testiamo su un mix di ENTRAMBE le board!
ACTIVE_BOARDS = [BOARD_324, BOARD_944]

# Split Temporale (Es. 80% del tempo addestramento, 20% test finale)
TRAIN_SPLIT_PERCENTAGE = 0.80

# ==========================================
# DATA FREQUENCY & TIME HORIZONS
# ==========================================
NOMINAL_FREQ_MINUTES = 6 
TARGET_FREQ_MINUTES = 30

# IL CUORE DEL SISTEMA: Giorni Virtuali (Es: 30 / 6 = 5)
VIRTUAL_RATIO = int(TARGET_FREQ_MINUTES / NOMINAL_FREQ_MINUTES)

SYNC_LOOKBACK_DAYS = "-30d"
INFERENCE_LOOKBACK_DAYS = "-7d"

# Orizzonte temporale (numero di sample) per addestrare gli ARIMA ambientali (~2 settimane a 6 min)
ENV_ARIMA_TRAIN_TAIL = 3360 

# ==========================================
# PREPROCESSING & INTERPOLATION
# ==========================================
DEFAULT_LAGS = 6

# Parametri per l'interpolazione Gaussiana
INTERPOLATION_WIN_BEFORE = 5
INTERPOLATION_WIN_AFTER = 2
INTERPOLATION_SIGMA_MIN = 15.0

LEAF_MAX_GAP_MINUTES = 12

# ==========================================
# ANOMALY DETECTION
# ==========================================
MIN_VALID_WATER_TEMP = 10.0
TDS_ROLLING_WINDOW = 10
TDS_SPIKE_THRESHOLD = 1.3  # 30% oltre la mediana mobile

# ==========================================
# INFERENCE API LIMITS
# ==========================================
MIN_HISTORY_RECORDS = (DEFAULT_LAGS * VIRTUAL_RATIO) + 2
FETCH_LIMIT_LATEST = MIN_HISTORY_RECORDS + 20
FETCH_LIMIT_MANUAL = FETCH_LIMIT_LATEST - 1

# MIN_HISTORY_RECORDS_LATEST = 7
# MIN_HISTORY_RECORDS_MANUAL = 6
# FETCH_LIMIT_LATEST = 60
# FETCH_LIMIT_MANUAL = 59