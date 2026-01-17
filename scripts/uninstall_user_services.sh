#!/usr/bin/env bash
set -euo pipefail

SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"

systemctl --user disable --now machinespirit-ui.service 2>/dev/null || true
systemctl --user disable --now machinespirit-api.service 2>/dev/null || true

rm -f "$SYSTEMD_USER_DIR/machinespirit-ui.service"
rm -f "$SYSTEMD_USER_DIR/machinespirit-api.service"

systemctl --user daemon-reload

echo "OK: services removed."
