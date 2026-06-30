#!/bin/bash
# InfluxDB eseguirà questo script automaticamente al primo avvio del container

FILE_CSV="/docker-entrypoint-initdb.d/cumulative_dump.csv"

if [ -f "$FILE_CSV" ]; then
    echo "🔄 Importazione automatica del dump iniziale da $FILE_CSV..."
    # Usiamo le variabili d'ambiente native del container di Influx
    influx write \
      --bucket "$DOCKER_INFLUXDB_INIT_BUCKET" \
      --org "$DOCKER_INFLUXDB_INIT_ORG" \
      --token "$DOCKER_INFLUXDB_INIT_ADMIN_TOKEN" \
      --format csv < "$FILE_CSV"
    echo "✅ Dati importati con successo al boot!"
else
    echo "⚠️ Nessun cumulative_dump.csv trovato. Il database parte vuoto."
fi