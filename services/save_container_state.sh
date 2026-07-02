#!/bin/bash

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Saving influx data..."
if [ -x "$DIR/influx/export_influx.sh" ]; then
    "$DIR/influx/export_influx.sh"
else
    echo "Error: $DIR/influx/export_influx.sh is missing or not executable."
    exit 1
fi

echo "Saving grafana configurations ..."
if [ -x "$DIR/grafana/export_grafana.sh" ]; then
    "$DIR/grafana/export_grafana.sh"
else
    echo "Error: $DIR/grafana/export_grafana.sh is missing or not executable."
    exit 1
fi

echo "Serialization statefull containers completed successfully."