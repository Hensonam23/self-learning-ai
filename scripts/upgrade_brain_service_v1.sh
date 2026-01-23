#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

echo "== upgrade_brain_service_v1 =="
echo "repo: $REPO_DIR"

mkdir -p systemd

cat > systemd/machinespirit-brain.service <<'UNIT'
[Unit]
Description=MachineSpirit Brain (headless loop)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/self-learning-ai
Environment=PYTHONUNBUFFERED=1
ExecStart=%h/self-learning-ai/.venv/bin/python %h/self-learning-ai/brain.py --headless
Restart=always
RestartSec=2
SyslogIdentifier=machinespirit-brain

# safety: avoid runaway
Nice=5

[Install]
WantedBy=default.target
UNIT

# install to user systemd
mkdir -p "$HOME/.config/systemd/user"
cp -a systemd/machinespirit-brain.service "$HOME/.config/systemd/user/machinespirit-brain.service"

systemctl --user daemon-reload
systemctl --user enable machinespirit-brain.service
systemctl --user restart machinespirit-brain.service

sleep 1
systemctl --user status machinespirit-brain.service --no-pager -l || true

echo "OK: upgrade_brain_service_v1 complete"
