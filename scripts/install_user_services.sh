#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

# Ensure venv exists
"$REPO_DIR/scripts/bootstrap_venv.sh"

# Ensure secrets exist
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

SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
mkdir -p "$SYSTEMD_USER_DIR"

render_unit() {
  local src="$1"
  local dst="$2"
  sed "s|@REPO_DIR@|$REPO_DIR|g" "$src" > "$dst"
}

render_unit "$REPO_DIR/systemd/machinespirit-api.service.in" "$SYSTEMD_USER_DIR/machinespirit-api.service"
render_unit "$REPO_DIR/systemd/machinespirit-ui.service.in"  "$SYSTEMD_USER_DIR/machinespirit-ui.service"

echo "[systemd] daemon-reload..."
systemctl --user daemon-reload

echo "[systemd] enable + start services..."
systemctl --user enable --now machinespirit-api.service
systemctl --user enable --now machinespirit-ui.service

echo
echo "OK: installed + started."
echo "Secrets file:"
echo "  $SECRETS_FILE"
echo
echo "Check status with:"
echo "  systemctl --user status machinespirit-api.service --no-pager"
echo "  systemctl --user status machinespirit-ui.service --no-pager"
