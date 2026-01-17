#!/usr/bin/env bash
set -euo pipefail

# MachineSpirit one-command installer
# Run from repo root:
#   ./scripts/install.sh

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

APP_USER="$USER"
VENV_DIR="$REPO_DIR/.venv"

CFG_DIR="$HOME/.config/machinespirit"
ENV_FILE="$CFG_DIR/api.env"

SYSTEMD_USER_DIR="$HOME/.config/systemd/user"

API_PORT="8010"
UI_PORT="8020"

echo "[1/9] Repo: $REPO_DIR"
echo "[1/9] Creating config dirs..."
mkdir -p "$CFG_DIR"
mkdir -p "$SYSTEMD_USER_DIR"

echo "[2/9] Ensuring Python venv..."
if [ ! -d "$VENV_DIR" ]; then
  /usr/bin/python3 -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "[3/9] Installing Python deps..."
python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements.txt

echo "[4/9] Generating / updating MS_API_KEY..."
NEW_KEY="$(python - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
)"

# Preserve existing lines except MS_API_KEY (replace it)
python - <<PY
from pathlib import Path

env_path = Path("${ENV_FILE}")
env_path.parent.mkdir(parents=True, exist_ok=True)

lines = []
if env_path.exists():
    lines = env_path.read_text(encoding="utf-8").splitlines()

out = []
found_key = False
found_python = False

for line in lines:
    if line.startswith("MS_API_KEY="):
        out.append("MS_API_KEY=" + ${NEW_KEY@Q})
        found_key = True
    elif line.startswith("MS_PYTHON="):
        out.append(line)
        found_python = True
    else:
        out.append(line)

if not found_python:
    out.append("MS_PYTHON=/usr/bin/python3")

if not found_key:
    out.append("MS_API_KEY=" + ${NEW_KEY@Q})

env_path.write_text("\n".join([l for l in out if l.strip() != ""]).strip() + "\n", encoding="utf-8")
print("Updated:", env_path)
PY

echo "[5/9] Writing systemd user service files (generated for this install path)..."

cat > "$SYSTEMD_USER_DIR/machinespirit-api.service" <<EOF
[Unit]
Description=MachineSpirit FastAPI (LAN)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$REPO_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$VENV_DIR/bin/python -m uvicorn ms_api:app --host 0.0.0.0 --port $API_PORT
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
EOF

cat > "$SYSTEMD_USER_DIR/machinespirit-ui.service" <<EOF
[Unit]
Description=MachineSpirit UI (LAN)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$REPO_DIR
Environment=MS_API_BASE=http://127.0.0.1:$API_PORT
EnvironmentFile=$ENV_FILE
ExecStart=$VENV_DIR/bin/python -m uvicorn ms_ui:app --host 0.0.0.0 --port $UI_PORT
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
EOF

echo "[6/9] Reloading systemd user + enabling services..."
systemctl --user daemon-reload
systemctl --user enable --now machinespirit-api.service
systemctl --user enable --now machinespirit-ui.service

echo "[7/9] Service status:"
systemctl --user status machinespirit-api.service --no-pager || true
systemctl --user status machinespirit-ui.service --no-pager || true

echo "[8/9] Quick health checks (local):"
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

curl -s "http://127.0.0.1:$API_PORT/health" -H "x-api-key: $MS_API_KEY" ; echo

echo "[9/9] Done."
LAN_IP="$(hostname -I | awk '{print $1}')"
echo "Open UI:"
echo "  http://$LAN_IP:$UI_PORT/ui/ask"
echo ""
echo "IMPORTANT: Your API key is stored in:"
echo "  $ENV_FILE"
echo "Do NOT commit that file."
