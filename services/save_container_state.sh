#!/bin/bash
# services/save_state.sh
# Questo script serializza sia i dati di InfluxDB che le Dashboard di Grafana
# preparandoli per un commit pulito su Git.

# Trova la cartella in cui si trova questo script (services/)
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "📦 Avvio della procedura di serializzazione del progetto..."
echo "--------------------------------------------------------"

# 1. Esportazione InfluxDB
echo "🗄️ [1/2] Salvataggio dello storico dati (InfluxDB)..."
if [ -x "$DIR/influx/export_influx.sh" ]; then
    "$DIR/influx/export_influx.sh"
else
    echo "❌ Errore: Lo script $DIR/influx/export_influx.sh non esiste o non ha i permessi di esecuzione."
    echo "   Prova a lanciare: chmod +x services/influx/export_influx.sh"
    exit 1
fi

echo ""

# 2. Esportazione Grafana
echo "📊 [2/2] Salvataggio delle configurazioni (Grafana)..."
if [ -x "$DIR/grafana/export_grafana.sh" ]; then
    "$DIR/grafana/export_grafana.sh"
else
    echo "❌ Errore: Lo script $DIR/grafana/export_grafana.sh non esiste o non ha i permessi di esecuzione."
    echo "   Prova a lanciare: chmod +x services/grafana/export_grafana.sh"
    exit 1
fi

echo "--------------------------------------------------------"
echo "✅ Serializzazione completata con successo!"
echo "👉 I tuoi file .csv e .json sono ora allineati allo stato attuale dei container."
echo ""
echo "Comandi suggeriti per la consegna:"
echo "  git status"
echo "  git add ."
echo "  git commit -m \"chore: salvataggio stato dashboard e storico dati pre-consegna\""
echo "  git push"