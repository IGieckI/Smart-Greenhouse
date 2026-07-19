const { InfluxDB, Point } = require('@influxdata/influxdb-client');
const config = require('./config');

const influxDB = new InfluxDB({ url: config.INFLUX_URL, token: config.INFLUX_TOKEN });
const writeApi = influxDB.getWriteApi(config.INFLUX_ORG, config.INFLUX_BUCKET);

async function writeTelemetry(node_id, data) {
    const point = new Point('sensor_measurements').tag('id_board', String(node_id));

    for (const field of config.NUMERIC_FIELDS) {
        if (data[field] !== undefined && !isNaN(data[field])) {
            point.floatField(field, Number(data[field]));
        }
    }

    writeApi.writePoint(point);
    await writeApi.flush();
}

module.exports = { writeTelemetry };