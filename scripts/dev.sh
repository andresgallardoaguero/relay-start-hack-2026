#!/usr/bin/env bash
# Script: dev.sh
# Purpose: Stop whatever runs on the app ports, then start the backend and the interface together, offline or against Viseca's server
# Author: Jonas Lüthi
# Date: September 2026
#
# Usage
#   scripts/dev.sh                  offline mode, keeps the local decisions
#   scripts/dev.sh offline --fresh  offline mode with an empty log and inbox, the old store is moved to outputs/state/backup-<time>/
#   scripts/dev.sh live             live mode against Viseca's server, needs TEAM_API_KEY in backend/.env and asks before starting
#   scripts/dev.sh live --yes       live mode without the question
#   scripts/dev.sh stop             only stops the backend and the interface
#
# Ports come from BACKEND_PORT and FRONTEND_PORT, with 8000 and 5173 as the defaults.
# Logs go to outputs/logs/backend-<port>.log and outputs/logs/frontend-<port>.log.

set -euo pipefail

REPOSITORY_FOLDER="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_FOLDER="$REPOSITORY_FOLDER/backend"
FRONTEND_FOLDER="$REPOSITORY_FOLDER/frontend"
LOG_FOLDER="$REPOSITORY_FOLDER/outputs/logs"
STATE_FOLDER="$REPOSITORY_FOLDER/outputs/state"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
LIVE_BASE_URL="https://leash-api-production.up.railway.app"









#### Step 1: Read the arguments ####

MODE="offline"
FRESH="no"
YES="no"
for argument in "$@"; do
    case "$argument" in
        offline|live|stop) MODE="$argument" ;;
        --fresh) FRESH="yes" ;;
        --yes) YES="yes" ;;
        -h|--help) sed -n '2,16p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "Unknown argument $argument. Use offline, live, stop, --fresh or --yes." >&2; exit 2 ;;
    esac
done









#### Step 2: Stop what runs on the two ports ####

# Stop every process that listens on a port, gently first and firmly after two seconds
stop_port() {
    local port="$1" label="$2" pids
    pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
    if [ -z "$pids" ]; then
        echo "Nothing runs on port $port ($label)."
        return
    fi
    echo "Stopping the $label on port $port (pid $(echo "$pids" | tr '\n' ' '))."
    kill $pids 2>/dev/null || true
    for _ in 1 2 3 4; do
        sleep 0.5
        if [ -z "$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)" ]; then
            return
        fi
    done
    kill -9 $pids 2>/dev/null || true
    sleep 0.5
}

stop_port "$BACKEND_PORT" "backend"
stop_port "$FRONTEND_PORT" "interface"
if [ "$MODE" = "stop" ]; then
    exit 0
fi









#### Step 3: Check the setup ####

if [ ! -x "$BACKEND_FOLDER/.venv/bin/python" ]; then
    echo "The backend environment is missing. Create it once with:" >&2
    echo "  python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.lock.txt" >&2
    exit 1
fi
if [ ! -d "$FRONTEND_FOLDER/node_modules" ]; then
    echo "The interface dependencies are missing. Install them once with:" >&2
    echo "  (cd frontend && npm install)" >&2
    exit 1
fi



# Live mode needs the platform key and a deliberate yes, because every live run counts against the allowance of the key
if [ "$MODE" = "live" ]; then
    if ! grep -qE '^TEAM_API_KEY=.+' "$BACKEND_FOLDER/.env" 2>/dev/null; then
        echo "Live mode needs TEAM_API_KEY in backend/.env. Copy backend/.env.example to backend/.env and fill it in." >&2
        exit 1
    fi
    echo
    echo "LIVE MODE talks to $LIVE_BASE_URL with the key from backend/.env."
    echo "Every scenario start counts against the run allowance of the key."
    if [ "$YES" != "yes" ]; then
        read -r -p "Type live to continue, anything else to stop: " answer
        if [ "$answer" != "live" ]; then
            echo "Not started."
            exit 0
        fi
    fi
fi



# A fresh start keeps the old store as a dated copy instead of deleting it
if [ "$FRESH" = "yes" ] && ls "$STATE_FOLDER"/relay.sqlite3* >/dev/null 2>&1; then
    backup_folder="$STATE_FOLDER/backup-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$backup_folder"
    mv "$STATE_FOLDER"/relay.sqlite3* "$backup_folder/"
    echo "Moved the old store to $backup_folder, the log and the inbox start empty."
fi

mkdir -p "$LOG_FOLDER" "$STATE_FOLDER"









#### Step 4: Start the backend ####

echo "Starting the backend in $MODE mode on port $BACKEND_PORT."
(
    cd "$BACKEND_FOLDER"
    LEASH_MODE="$MODE" \
    WEB_CORS_ORIGINS="http://localhost:$FRONTEND_PORT,http://127.0.0.1:$FRONTEND_PORT" \
    nohup .venv/bin/python -m uvicorn app.main:app --port "$BACKEND_PORT" > "$LOG_FOLDER/backend-$BACKEND_PORT.log" 2>&1 &
    echo $! > "$LOG_FOLDER/backend-$BACKEND_PORT.pid"
)
for _ in $(seq 1 60); do
    if curl -sf "http://127.0.0.1:$BACKEND_PORT/healthz" >/dev/null 2>&1; then
        break
    fi
    sleep 0.5
done
if ! curl -sf "http://127.0.0.1:$BACKEND_PORT/healthz" >/dev/null 2>&1; then
    echo "The backend did not come up. The last lines of $LOG_FOLDER/backend-$BACKEND_PORT.log:" >&2
    tail -20 "$LOG_FOLDER/backend-$BACKEND_PORT.log" >&2
    exit 1
fi



# Show which platform the backend talks to, so a wrong mode is visible before the first run
status="$(curl -sf "http://127.0.0.1:$BACKEND_PORT/api/status")"
"$BACKEND_FOLDER/.venv/bin/python" - "$status" <<'EOF'
import json, sys
status = json.loads(sys.argv[1])
print("  leash_mode        " + str(status.get("leash_mode")))
print("  leash_base_url    " + str(status.get("leash_base_url")))
print("  team_key_present  " + str(status.get("team_key_present")))
print("  llm_mode          " + str(status.get("llm_mode")))
print("  bootstrap_error   " + str(status.get("bootstrap_error")))
EOF









#### Step 5: Start the interface ####

echo "Starting the interface on port $FRONTEND_PORT."
(
    cd "$FRONTEND_FOLDER"
    VITE_USE_MOCK_API=false \
    VITE_API_BASE_URL="http://127.0.0.1:$BACKEND_PORT" \
    nohup npx vite --port "$FRONTEND_PORT" --strictPort > "$LOG_FOLDER/frontend-$FRONTEND_PORT.log" 2>&1 &
    echo $! > "$LOG_FOLDER/frontend-$FRONTEND_PORT.pid"
)
for _ in $(seq 1 60); do
    if [ -n "$(lsof -tiTCP:"$FRONTEND_PORT" -sTCP:LISTEN 2>/dev/null || true)" ]; then
        break
    fi
    sleep 0.5
done
if [ -z "$(lsof -tiTCP:"$FRONTEND_PORT" -sTCP:LISTEN 2>/dev/null || true)" ]; then
    echo "The interface did not come up. The last lines of $LOG_FOLDER/frontend-$FRONTEND_PORT.log:" >&2
    tail -20 "$LOG_FOLDER/frontend-$FRONTEND_PORT.log" >&2
    exit 1
fi

echo
echo "Interface   http://localhost:$FRONTEND_PORT"
echo "Backend     http://127.0.0.1:$BACKEND_PORT/api/status  (endpoints at /docs)"
echo "Logs        $LOG_FOLDER/backend-$BACKEND_PORT.log and $LOG_FOLDER/frontend-$FRONTEND_PORT.log"
echo "Stop with   scripts/dev.sh stop"
