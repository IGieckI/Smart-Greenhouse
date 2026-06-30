#!/bin/bash
# Estrae TUTTE le dashboard attuali e le salva in JSON separati

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Carica il .env se esiste
if [ -f "$DIR/../.env" ]; then
    source "$DIR/../.env"
fi

# Usa le credenziali dal .env, altrimenti fallback su admin
G_USER=${GRAFANA_USER:-admin}
G_PASS=${GRAFANA_PASSWORD:-adminadmin}

DEST_DIR="$DIR/exported_dashboards"
mkdir -p "$DEST_DIR"

echo "🔍 Ricerca delle dashboard in Grafana (http://127.0.0.1:3030)..."
echo "🔑 Tentativo di accesso con utente: $G_USER"

# Esegue la chiamata API e separa il codice di stato HTTP dalla risposta
HTTP_RESPONSE=$(curl -s -w "HTTPSTATUS:%{http_code}" -u "$G_USER:$G_PASS" "http://127.0.0.1:3030/api/search")
HTTP_BODY=$(echo "$HTTP_RESPONSE" | sed -e 's/HTTPSTATUS\:.*//g')
HTTP_STATUS=$(echo "$HTTP_RESPONSE" | tr -d '\n' | sed -e 's/.*HTTPSTATUS://')

if [ "$HTTP_STATUS" -eq 401 ]; then
    echo "❌ ERRORE: Grafana ha rifiutato la password (401 Unauthorized)."
    echo "👉 SOLUZIONE: Hai cambiato la password dal browser? Aggiorna GRAFANA_PASSWORD nel file services/.env!"
    exit 1
elif [ "$HTTP_STATUS" -ne 200 ]; then
    echo "❌ ERRORE API GRAFANA (Codice HTTP: $HTTP_STATUS)"
    echo "Risposta: $HTTP_BODY"
    exit 1
fi

# Estrae gli ID delle dashboard (escludendo le cartelle)
DASH_UIDS=$(echo "$HTTP_BODY" | jq -r '.[] | select(.type == "dash-db") | .uid')

if [ -z "$DASH_UIDS" ] || [ "$DASH_UIDS" == "null" ]; then
    echo "⚠️ Nessuna dashboard trovata nell'API. Risposta grezza dal server:"
    echo "$HTTP_BODY"
    exit 0
fi

# CAMBIAMENTO QUI: Usiamo DASH_ID invece della variabile protetta UID
for DASH_ID in $DASH_UIDS; do
    # Estrae il titolo in modo sicuro e converte gli spazi in underscore
    TITLE=$(curl -s -u "$G_USER:$G_PASS" "http://127.0.0.1:3030/api/dashboards/uid/$DASH_ID" | jq -r '.dashboard.title' | sed 's/[^a-zA-Z0-9]/_/g')
    
    # Scarica il JSON della dashboard pronto per il provisioning
    curl -s -u "$G_USER:$G_PASS" "http://127.0.0.1:3030/api/dashboards/uid/$DASH_ID" \
      | jq '.dashboard | .id = null' > "$DEST_DIR/${TITLE}.json"
      
    echo "✅ Dashboard salvata correttamente in: exported_dashboards/${TITLE}.json"
done

echo "🎉 Esportazione completata con successo!"