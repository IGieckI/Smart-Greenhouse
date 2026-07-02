#!/bin/bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "$DIR/../.env" ]; then
    source "$DIR/../.env"
fi

G_USER=${GRAFANA_USER:-admin}
G_PASS=${GRAFANA_PASSWORD:-adminadmin}

DEST_DIR="$DIR/exported_dashboards"
mkdir -p "$DEST_DIR"

echo "Access into grafana container with user: $G_USER"

HTTP_RESPONSE=$(curl -s -w "HTTPSTATUS:%{http_code}" -u "$G_USER:$G_PASS" "http://127.0.0.1:3030/api/search")
HTTP_BODY=$(echo "$HTTP_RESPONSE" | sed -e 's/HTTPSTATUS\:.*//g')
HTTP_STATUS=$(echo "$HTTP_RESPONSE" | tr -d '\n' | sed -e 's/.*HTTPSTATUS://')

if [ "$HTTP_STATUS" -eq 401 ]; then
    echo "Grafana has refused password (401 Unauthorized)."
    exit 1
elif [ "$HTTP_STATUS" -ne 200 ]; then
    echo "Grafana API error (HTTP code: $HTTP_STATUS)"
    echo "Details: $HTTP_BODY"
    exit 1
fi

DASH_UIDS=$(echo "$HTTP_BODY" | jq -r '.[] | select(.type == "dash-db") | .uid')

if [ -z "$DASH_UIDS" ] || [ "$DASH_UIDS" == "null" ]; then
    echo "No dashboard found in the API"
    echo "Details: $HTTP_BODY"
    exit 0
fi

for DASH_ID in $DASH_UIDS; do
    TITLE=$(curl -s -u "$G_USER:$G_PASS" "http://127.0.0.1:3030/api/dashboards/uid/$DASH_ID" | jq -r '.dashboard.title' | sed 's/[^a-zA-Z0-9]/_/g')
    
    curl -s -u "$G_USER:$G_PASS" "http://127.0.0.1:3030/api/dashboards/uid/$DASH_ID" \
      | jq '.dashboard | .id = null' > "$DEST_DIR/${TITLE}.json"
      
    echo "Dashboard storing with success in exported_dashboards/${TITLE}.json"
done
