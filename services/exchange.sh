#!/bin/bash

# Parametri di configurazione (presi dal tuo docker-compose)
ORG="iot_org"
BUCKET="sensor_data"
TOKEN="TokenFittizio"
CONTAINER_NAME="influxdb"
# Cambiamo il nome del file per rendere chiaro che contiene TUTTO
CUMULATIVE_DUMP_FILE="cumulative_dump.csv"

# Controllo argomenti
FRIEND_CSV=$1

if [ -z "$FRIEND_CSV" ]; then
  echo "⚠️  Errore: Nessun file fornito."
  echo "👉 Uso corretto: ./sync_data.sh <percorso_csv_del_tuo_amico>"
  exit 1
fi

if [ ! -f "$FRIEND_CSV" ]; then
  echo "⚠️  Errore: Il file '$FRIEND_CSV' non esiste o il percorso è errato."
  exit 1
fi

echo "🔄 [1/2] Importazione dei dati nel database locale..."
# 1. IMPORTAZIONE: InfluxDB fa l'Upsert automatico (aggiunge i nuovi, sovrascrive gli identici)
docker exec -i $CONTAINER_NAME influx write \
  --bucket $BUCKET \
  --org $ORG \
  --token $TOKEN \
  --format csv < "$FRIEND_CSV"

if [ $? -eq 0 ]; then
    echo "✅ Importazione completata! Il tuo InfluxDB ora contiene l'unione dei dati."
else
    echo "❌ Errore durante l'importazione dei dati."
    exit 1
fi

echo "📊 [2/2] Creazione del dump cumulativo..."
# 2. ESPORTAZIONE: Peschiamo dal DB tutto lo storico, che ora include anche i dati appena importati
docker exec -it $CONTAINER_NAME influx query \
  "from(bucket:\"$BUCKET\") |> range(start: 0)" \
  --org $ORG \
  --token $TOKEN \
  --raw > $CUMULATIVE_DUMP_FILE

if [ $? -eq 0 ]; then
    echo "✅ Dump completato! File pronto per la prossima condivisione: $CUMULATIVE_DUMP_FILE"
else
    echo "❌ Errore durante la creazione del dump."
    exit 1
fi