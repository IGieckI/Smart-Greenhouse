#!/bin/bash
# Scarica tutto il contenuto del database e sovrascrive il file CSV

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DIR/../.env"

docker exec influxdb influx query \
  "from(bucket:\"${INFLUX_BUCKET}\") |> range(start: 0)" \
  --org "${INFLUX_ORG}" \
  --token "${INFLUX_TOKEN}" \
  --raw > "$DIR/cumulative_dump.csv"

echo "✅ Dati di InfluxDB aggiornati in services/influx/cumulative_dump.csv"