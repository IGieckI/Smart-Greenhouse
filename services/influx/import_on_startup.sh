#!/bin/bash

FILE_CSV_RAW="/docker-entrypoint-initdb.d/cumulative_dump.csv"
FILE_CSV_CAVEAUX="/docker-entrypoint-initdb.d/caveaux.csv"

if [ -f "$FILE_CSV_RAW" ]; then
    echo "Automatic import of raw data dump from $FILE_CSV_RAW..."

    influx write \
      --bucket "$DOCKER_INFLUXDB_INIT_BUCKET" \
      --org "$DOCKER_INFLUXDB_INIT_ORG" \
      --token "$DOCKER_INFLUXDB_INIT_ADMIN_TOKEN" \
      --format csv < "$FILE_CSV_RAW"
    echo "Raw data import complete"
else
    echo "No dump of raw data found. Influx database will be empty."
fi


if [ -f "$FILE_CSV_CAVEAUX" ]; then
    echo "Automatic import of forecast freeze dump from $FILE_CSV_CAVEAUX..."

    influx bucket create \
      --name "$DOCKER_INFLUXDB_CAVEAUX_BUCKET" \
      --org "$DOCKER_INFLUXDB_INIT_ORG" \
      --token "$DOCKER_INFLUXDB_INIT_ADMIN_TOKEN" || true

    influx write \
      --bucket "$DOCKER_INFLUXDB_CAVEAUX_BUCKET" \
      --org "$DOCKER_INFLUXDB_INIT_ORG" \
      --token "$DOCKER_INFLUXDB_INIT_ADMIN_TOKEN" \
      --format csv < "$FILE_CSV_CAVEAUX"
    echo "Forecast freeze data import complete"
else
    echo "No dump of caveaux bucket found."
fi