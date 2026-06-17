
USER="JP"
FILE_NAME="${USER}_data.csv"
echo ${FILE_NAME}

# docker exec -it influxdb influx query 'from(bucket:"sensor_data") |> range(start: 0)' --org iot_org --token TokenFittizio --raw > ${FILE_NAME}

docker exec -i influxdb influx write --bucket sensor_data --org iot_org --token TokenFittizio --format csv < ${FILE_NAME}