#!/bin/bash

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DIR/../.env"

docker exec influxdb influx query \
  "from(bucket:\"${INFLUX_BUCKET_RAW}\") |> range(start: 0)" \
  --org "${INFLUX_ORG}" \
  --token "${INFLUX_TOKEN}" \
  --raw > "$DIR/cumulative_dump.csv"

# docker exec influxdb influx query \
#   "from(bucket:\"${INFLUX_BUCKET_CAVEAUX}\") |> range(start: 0)" \
#   --org "${INFLUX_ORG}" \
#   --token "${INFLUX_TOKEN}" \
#   --raw > "$DIR/caveaux.csv"

echo "InfluxDB data dumped"