#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "== MachineSpirit Boot Runner =="
echo "when: $(date -Is)"
echo "repo: $(pwd)"

# If an upgrade is in progress, don't interfere.
LOCK_FILE="data/runtime/maintenance.lock"
if [ -f "$LOCK_FILE" ]; then
  now=$(date +%s)
  ts=$(stat -c %Y "$LOCK_FILE" 2>/dev/null || echo 0)
  age=$(( now - ts ))
  if [ "$age" -lt 3600 ]; then
    echo "BOOT: maintenance lock present (age=${age}s) -> will NOT apply proposals"
    APPLY_OK="no"
  else
    echo "BOOT: stale maintenance lock (age=${age}s) -> removing"
    rm -f "$LOCK_FILE" || true
    APPLY_OK="yes"
  fi
else
  APPLY_OK="yes"
fi

echo
echo "== boot: selftest (pre) =="
./scripts/selftest.sh

echo
echo "== boot: reflection =="
/usr/bin/python3 scripts/reflect.py || true

echo
echo "== boot: auto_propose =="
/usr/bin/python3 scripts/auto_propose.py || true

echo
echo "== boot: apply 1 pending proposal (optional) =="
if [ "$APPLY_OK" = "yes" ]; then
  ./scripts/apply_latest_proposal.sh || true
else
  echo "BOOT: skipping apply_latest_proposal.sh due to maintenance lock"
fi

echo
echo "== boot: selftest (post) =="
./scripts/selftest.sh

echo
echo "== boot: done =="
echo "when: $(date -Is)"
