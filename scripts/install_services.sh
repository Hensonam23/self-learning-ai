#!/usr/bin/env bash
set -euo pipefail

echo "[1/4] Creating user systemd folder..."
mkdir -p "$HOME/.config/systemd/user"

echo "[2/4] Installing service files..."
install -m 0644 systemd/user/machinespirit-api.service "$HOME/.config/systemd/user/" 2>/dev/null || true
install -m 0644 systemd/user/machinespirit-ui.service  "$HOME/.config/systemd/user/"

echo "[3/4] Reload + enable services..."
systemctl --user daemon-reload
systemctl --user enable --now machinespirit-api.service 2>/dev/null || true
systemctl --user enable --now machinespirit-ui.service

echo "[4/4] Done. Status:"
systemctl --user status machinespirit-api.service --no-pager || true
systemctl --user status machinespirit-ui.service --no-pager
