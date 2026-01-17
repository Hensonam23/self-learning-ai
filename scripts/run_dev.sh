#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

if [ ! -d ".venv" ]; then
  echo "No .venv found. Running bootstrap..."
  "$REPO_DIR/scripts/bootstrap_venv.sh"
fi

# shellcheck disable=SC1091
source .venv/bin/activate

# Ensure secrets exist (same as systemd)
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

# Load MS_API_KEY into this shell so ms_api works in dev mode too
set -a
# shellcheck disable=SC1090
source "$SECRETS_FILE"
set +a

API_HOST="${MS_API_HOST:-0.0.0.0}"
API_PORT="${MS_API_PORT:-8010}"
UI_HOST="${MS_UI_HOST:-0.0.0.0}"
UI_PORT="${MS_UI_PORT:-8020}"

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

echo
echo "OK: services running."
echo "UI:  http://<this-pi-ip>:${UI_PORT}/ui"
echo "API: http://127.0.0.1:${API_PORT}/health (requires X-API-Key)"
echo
echo "Press Ctrl+C to stop."
wait
