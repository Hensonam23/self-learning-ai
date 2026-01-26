#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== upgrade_autoapply_v1 =="

UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"

install -m 0644 systemd/machinespirit-autoapply.service "$UNIT_DIR/machinespirit-autoapply.service"
install -m 0644 systemd/machinespirit-autoapply.timer   "$UNIT_DIR/machinespirit-autoapply.timer"

systemctl --user daemon-reload
systemctl --user enable machinespirit-autoapply.timer >/dev/null 2>&1 || true
systemctl --user restart machinespirit-autoapply.timer >/dev/null 2>&1 || true

# Kick one run now so we know it works
systemctl --user start machinespirit-autoapply.service || true
systemctl --user status machinespirit-autoapply.service --no-pager -l || true

echo "OK: upgrade_autoapply_v1 complete"
