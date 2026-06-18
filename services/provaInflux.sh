#!/bin/bash

docker exec -it influxdb influx query \
  --token TokenFittizio \
  --org iot_org \
  'from(bucket: "sensor_data") |> range(start: -1h) |> filter(fn: (r) => r["_measurement"] == "ambient_data") |> filter(fn: (r) => r["_field"] == "temperature" or r["_field"] == "humidity") |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)'