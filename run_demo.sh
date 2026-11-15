#!/usr/bin/env bash
# One command for the demo: the backend (which runs the simulator in-process) and the dashboard. Ctrl-C stops both.
#   ./run_demo.sh                                   backend :8100, dashboard :5180
#   BACKEND_PORT=8111 FRONTEND_PORT=5191 ./run_demo.sh
#   NO_BROWSER=1 ./run_demo.sh                      don't open the browser
set -euo pipefail
cd "$(dirname "$0")"
BACKEND_PORT=${BACKEND_PORT:-8100}
FRONTEND_PORT=${FRONTEND_PORT:-5180}

for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port $port is already in use:" >&2
    lsof -nP -iTCP:"$port" -sTCP:LISTEN >&2 || true
    echo "Stop that process yourself, or pick other ports: BACKEND_PORT=... FRONTEND_PORT=... ./run_demo.sh" >&2
    exit 1
  fi
done
[ -x backend/.venv/bin/python ] || { echo "Missing backend/.venv (see README: Setup)." >&2; exit 1; }
[ -x frontend/node_modules/.bin/vite ] || { echo "Missing frontend/node_modules (run: cd frontend && npm install)." >&2; exit 1; }

if [ -f .env ]; then set -a; . ./.env; set +a; fi  # API keys; every service has an offline fallback without them

pids=()
stop() {
  trap - INT TERM EXIT
  echo; echo "Stopping..."
  kill "${pids[@]}" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap stop INT TERM EXIT

wait_for() {  # url name
  for _ in $(seq 120); do
    curl -fsS -o /dev/null "$1" 2>/dev/null && { echo "$2 is up: $1"; return 0; }
    for p in "${pids[@]}"; do kill -0 "$p" 2>/dev/null || { echo "$2 failed to start." >&2; exit 1; }; done
    sleep 0.5
  done
  echo "$2 did not answer within 60 s." >&2
  exit 1
}
(cd backend && exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT") &
pids+=($!)
wait_for "http://127.0.0.1:$BACKEND_PORT/health" "Backend"
(cd frontend && BACKEND_PORT="$BACKEND_PORT" exec node_modules/.bin/vite --host 127.0.0.1 --port "$FRONTEND_PORT" --strictPort --clearScreen false) &
pids+=($!)
wait_for "http://127.0.0.1:$FRONTEND_PORT/" "Dashboard"

URL="http://localhost:$FRONTEND_PORT"
echo "Tower Zero is running at $URL (simulation only). Ctrl-C to stop."
[ -n "${NO_BROWSER:-}" ] || open "$URL" 2>/dev/null || true
wait
