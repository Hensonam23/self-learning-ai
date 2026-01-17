#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

# Ensure venv exists
if [ ! -d ".venv" ]; then
  echo "No .venv found. Running bootstrap..."
  "$REPO_DIR/scripts/bootstrap_venv.sh"
fi

# shellcheck disable=SC1091
source .venv/bin/activate

# Ensure secrets exist (same location as systemd)
CFG_DIR="${HOME}/.config/machinespirit"
SECRETS_FILE="${CFG_DIR}/secrets.env"
mkdir -p "$CFG_DIR"

if [ ! -f "$SECRETS_FILE" ]; then
  python3 - <<'PY'
import os, secrets
cfg_dir = os.path.expanduser("~/.config/machinespirit")
path = os.path.join(cfg_dir, "secrets.env")
os.makedirs(cfg_dir, exist_ok=True)
key = secrets.token_hex(24)
with open(path, "w", encoding="utf-8") as f:
    f.write(f"MS_API_KEY={key}\n")
os.chmod(path, 0o600)
print("Created secrets:", path)
PY
else
  chmod 600 "$SECRETS_FILE" 2>/dev/null || true
fi

# Load env vars from secrets for dev mode too
set -a
# shellcheck disable=SC1090
source "$SECRETS_FILE"
set +a

API_HOST="${MS_API_HOST:-0.0.0.0}"
API_PORT="${MS_API_PORT:-8010}"
UI_HOST="${MS_UI_HOST:-0.0.0.0}"
UI_PORT="${MS_UI_PORT:-8020}"

port_in_use() {
  local p="$1"
  ss -ltnp 2>/dev/null | grep -qE ":${p}\b"
}

show_port_owner() {
  local p="$1"
  ss -ltnp 2>/dev/null | grep -E ":${p}\b" || true
}

# If systemd services are running, dev mode will fail. Catch it early and explain.
if port_in_use "$API_PORT" || port_in_use "$UI_PORT"; then
  echo
  echo "ERROR: One of the ports is already in use."
  echo
  if port_in_use "$API_PORT"; then
    echo "Port ${API_PORT} is in use:"
    show_port_owner "$API_PORT"
    echo
  fi
  if port_in_use "$UI_PORT"; then
    echo "Port ${UI_PORT} is in use:"
    show_port_owner "$UI_PORT"
    echo
  fi

  echo "If you installed the systemd user services, stop them first:"
  echo "  systemctl --user stop machinespirit-api.service machinespirit-ui.service"
  echo
  echo "Then re-run:"
  echo "  ./scripts/run_dev.sh"
  echo
  exit 1
fi

echo "[dev] starting API on ${API_HOST}:${API_PORT} ..."
python -m uvicorn ms_api:app --host "$API_HOST" --port "$API_PORT" &
API_PID=$!

echo "[dev] starting UI on ${UI_HOST}:${UI_PORT} ..."
python -m uvicorn ms_ui:app --host "$UI_HOST" --port "$UI_PORT" &
UI_PID=$!

cleanup() {
  echo
  echo "[dev] stopping..."
  kill "$UI_PID" 2>/dev/null || true
  kill "$API_PID" 2>/dev/null || true
  wait "$UI_PID" 2>/dev/null || true
  wait "$API_PID" 2>/dev/null || true
}
trap cleanup INT TERM

# Give uvicorn a moment to bind
sleep 0.4

if ! kill -0 "$API_PID" 2>/dev/null; then
  echo
  echo "ERROR: API process died right after start."
  echo "Try running the API alone to see the real error:"
  echo "  python -m uvicorn ms_api:app --host $API_HOST --port $API_PORT"
  echo
  exit 1
fi

if ! kill -0 "$UI_PID" 2>/dev/null; then
  echo
  echo "ERROR: UI process died right after start."
  echo "Try running the UI alone to see the real error:"
  echo "  python -m uvicorn ms_ui:app --host $UI_HOST --port $UI_PORT"
  echo
  exit 1
fi

echo
echo "OK: services running (dev mode)."
echo "UI:  http://<this-pi-ip>:${UI_PORT}/ui"
echo "API: http://127.0.0.1:${API_PORT}/health (requires X-API-Key)"
echo
echo "API key file:"
echo "  ${SECRETS_FILE}"
echo
echo "Example health check:"
echo "  MS_API_KEY=\"\$(grep -m1 '^MS_API_KEY=' ${SECRETS_FILE} | cut -d= -f2-)\""
echo "  curl -s http://127.0.0.1:${API_PORT}/health -H \"X-API-Key: \$MS_API_KEY\" ; echo"
echo
echo "Press Ctrl+C to stop."
wait
