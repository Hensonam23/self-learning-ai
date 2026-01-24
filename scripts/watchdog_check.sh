#!/usr/bin/env bash
# --- MS_MAINT_LOCK_V2 ---
LOCK_FILE="data/runtime/maintenance.lock"
if [ -f "$LOCK_FILE" ]; then
  now=$(date +%s)
  ts=$(stat -c %Y "$LOCK_FILE" 2>/dev/null || echo 0)
  age=$(( now - ts ))
  if [ "$age" -lt 3600 ]; then
    echo "WATCHDOG: maintenance lock present (age=${age}s) -> skipping"
    exit 0
  else
    echo "WATCHDOG: stale maintenance lock (age=${age}s) -> removing"
    rm -f "$LOCK_FILE" || true
  fi
fi

# --- MS_MAINT_LOCK_V1 ---
LOCK_FILE="data/runtime/maintenance.lock"
if [ -f "$LOCK_FILE" ]; then
  now=$(date +%s)
  ts=$(stat -c %Y "$LOCK_FILE" 2>/dev/null || echo 0)
  age=$(( now - ts ))
  if [ "$age" -lt 3600 ]; then
    echo "WATCHDOG: maintenance lock present (age=${age}s) -> skipping"
    exit 0
  else
    echo "WATCHDOG: stale maintenance lock (age=${age}s) -> removing"
    rm -f "$LOCK_FILE" || true
  fi
fi

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

MS_API_KEY="$(grep -m1 '^MS_API_KEY=' ~/.config/machinespirit/secrets.env | cut -d= -f2- || true)"

check_api () {
  curl -fsS -m 2 http://127.0.0.1:8010/health -H "X-API-Key: $MS_API_KEY" >/dev/null
}
check_ui () {
  curl -fsS -m 2 http://127.0.0.1:8020/health >/dev/null
}

ok=1

if ! check_api; then
  echo "WATCHDOG: API health failed -> restarting machinespirit-api.service"
  systemctl --user restart machinespirit-api.service || true
  sleep 2
  check_api || ok=0
fi

if ! check_ui; then
  echo "WATCHDOG: UI health failed -> restarting machinespirit-ui.service"
  systemctl --user restart machinespirit-ui.service || true
  sleep 2
  check_ui || ok=0
fi

if [ "$ok" -ne 1 ]; then
  echo "WATCHDOG: health still failing -> running selftest for debug"
  ./scripts/selftest.sh || true
  exit 1
fi

echo "WATCHDOG: ok"
