#!/bin/bash
# greenhouse.sh — manage the Smart Greenhouse backend stack
#
# Commands:
#   setup               Create .env from template and validate dependencies
#   up [profile]        Start services  (profiles: core | ml | all — default: all)
#   down                Stop all services
#   logs [service]      Follow logs (optional: name of one service)
#   reset <service>     Tear down and rebuild a single service
#   exchange <file.csv> Import a plain CSV with dumped data and export a merged dump

set -euo pipefail

COMPOSE="docker compose"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Helpers
info()  { echo "[INFO]  $*"; }
warn()  { echo "[WARN]  $*"; }
die()   { echo "[ERROR] $*" >&2; exit 1; }

# Check dependencies: Docker and docker compose v2
check_deps() {
    command -v docker  >/dev/null 2>&1 || die "docker not found. Install Docker Desktop or Docker Engine."
    docker compose version >/dev/null 2>&1 || die "'docker compose' (v2) not found. Update Docker."
}

# Create .env from template if missing
ensure_env() {
    if [[ ! -f .env ]]; then
        warn ".env not found — creating from template. Fill in TELEGRAM_TOKEN before running 'up'."
        cat > .env <<'EOF'
# InfluxDB admin token (must match DOCKER_INFLUXDB_INIT_ADMIN_TOKEN in docker-compose.yml)
INFLUX_TOKEN=TokenFittizio

# Telegram bot token — get one from @BotFather on Telegram
TELEGRAM_TOKEN=your_telegram_bot_token_here
EOF
        info ".env created at services/.env"
    fi
}

# Commands

# Setup: check dependencies and create .env if missing
cmd_setup() {
    check_deps
    ensure_env
    info "Dependencies OK."
    info "Next step: edit .env if you haven't, then run:  ./greenhouse.sh up"
}

# Start services based on profile
cmd_up() {
    local profile="${1:-all}"
    check_deps
    ensure_env

    case "$profile" in
        core)
            info "Starting core services (influxdb, grafana, mosquitto, controller, lw-client, cw-client, tg-bot)..."
            $COMPOSE up -d influxdb grafana mosquitto controller lw-client cw-client tg-bot
            ;;
        ml)
            info "Starting ML pipeline (influxdb + ml-trainer + ml-inference)..."
            $COMPOSE up -d influxdb ml-trainer ml-inference
            ;;
        all)
            info "Starting all services..."
            $COMPOSE up -d
            ;;
        *)
            die "Unknown profile '$profile'. Use: core | ml | all"
            ;;
    esac

    echo ""
    info "Services running:"
    info "  Grafana         →  http://localhost:3030  (admin/admin on first login)"
    info "  InfluxDB        →  http://localhost:8086  (token: TokenFittizio)"
    info "  Controller API  →  http://localhost:3001/api/data"
    info "  ML Inference    →  http://localhost:8000  (if ml or all profile)"
    info "  MQTT Broker     →  localhost:1883"
}

# Stop all services
cmd_down() {
    info "Stopping all services..."
    $COMPOSE down
}

# Follow logs for all services or a specific one
cmd_logs() {
    local svc="${1:-}"
    if [[ -n "$svc" ]]; then
        $COMPOSE logs -f "$svc"
    else
        $COMPOSE logs -f
    fi
}

# Reset a single service: down + up with rebuild
cmd_reset() {
    local svc="${1:-}"
    [[ -z "$svc" ]] && die "Specify a service name. Example: ./greenhouse.sh reset lw-client"
    info "Resetting service: $svc"
    $COMPOSE down "$svc"
    $COMPOSE up -d --build --force-recreate "$svc"
    $COMPOSE logs -f "$svc"
}

# Send a command to a node via the controller API
cmd_command() {
    local node_id="${1:-}"
    local actuator="${2:-}"
    local value="${3:-}"
    local duration="${4:-10}"
    [[ -z "$node_id" || -z "$actuator" || -z "$value" ]] && \
        die "Usage: ./greenhouse.sh command <node_id> <actuator> <value 0-100> [duration_s]
  Examples:
    ./greenhouse.sh command 3750866944 pump 100 10   # pump on full for 10s
    ./greenhouse.sh command 3750866944 led  75  30   # LED at 75% for 30s
    ./greenhouse.sh command 3750866944 pump 0   0    # pump off immediately"
    info "Sending command to node $node_id: $actuator=$value% for ${duration}s..."
    curl -sf -X POST http://localhost:3001/api/command \
        -H "Content-Type: application/json" \
        -d "{\"node_id\":$node_id,\"actuator\":\"$actuator\",\"value\":$value,\"duration_s\":$duration}" \
        | (command -v jq >/dev/null 2>&1 && jq || cat)
}

# Exchange: import a CSV dump and export a merged cumulative dump
cmd_exchange() {
    local csv="${1:-}"
    [[ -z "$csv" ]] && die "Specify a CSV file. Example: ./greenhouse.sh exchange friend_dump.csv"
    [[ ! -f "$csv" ]] && die "File not found: $csv"

    local org="iot_org"
    local bucket="sensor_data"
    local token="TokenFittizio"
    local dump="cumulative_dump.csv"

    info "Importing data from $csv into InfluxDB..."
    docker exec -i influxdb influx write \
        --bucket "$bucket" --org "$org" --token "$token" --format csv < "$csv" \
        || die "Import failed."

    info "Exporting merged cumulative dump to $dump..."
    docker exec -it influxdb influx query \
        "from(bucket:\"$bucket\") |> range(start: 0)" \
        --org "$org" --token "$token" --raw > "$dump" \
        || die "Export failed."

    info "Done. Merged dump saved to services/$dump"
}


CMD="${1:-help}"
shift || true

case "$CMD" in
    setup)    cmd_setup ;;
    up)       cmd_up    "${1:-all}" ;;
    down)     cmd_down ;;
    logs)     cmd_logs  "${1:-}" ;;
    reset)    cmd_reset "${1:-}" ;;
    exchange) cmd_exchange "${1:-}" ;;
    command)  cmd_command "${1:-}" "${2:-10}" "${3:-on}" ;;
    help|--help|-h)
        echo "Usage: ./greenhouse.sh <command> [options]"
        echo ""
        echo "Commands:"
        echo "  setup                        First-time setup: create .env, check Docker"
        echo "  up [core|ml|all]             Start services (default: all)"
        echo "  down                         Stop all services"
        echo "  logs [service]               Follow logs (all services, or one by name)"
        echo "  reset <service>              Rebuild and restart a single service"
        echo "  exchange <file.csv>          Import colleague CSV, export merged dump"
        echo "  command <node_id> <act> <val 0-100> [dur]   Send actuator command (default dur: 10s)"
        ;;
    *)
        die "Unknown command '$CMD'. Run './greenhouse.sh help' for usage."
        ;;
esac
