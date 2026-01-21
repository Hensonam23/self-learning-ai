#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

STAMP="$(date +%Y%m%d-%H%M%S)"
BKDIR="data/backups/guarded_apply/$STAMP"
mkdir -p "$BKDIR"

echo "== guarded_apply: stopping services =="
systemctl --user stop machinespirit-api.service machinespirit-ui.service || true

echo "== guarded_apply: backing up files to $BKDIR =="
cp -a ms_api.py "$BKDIR/ms_api.py"
cp -a ms_ui.py  "$BKDIR/ms_ui.py"
cp -a brain.py  "$BKDIR/brain.py" || true

echo "== guarded_apply: running upgrade command =="
set +e
bash -lc "$*"
RC=$?
set -e

if [ $RC -ne 0 ]; then
  echo "FAIL: upgrade command failed (rc=$RC) — restoring backups"
  cp -a "$BKDIR/ms_api.py" ms_api.py
  cp -a "$BKDIR/ms_ui.py"  ms_ui.py
  [ -f "$BKDIR/brain.py" ] && cp -a "$BKDIR/brain.py" brain.py || true
fi

echo "== guarded_apply: starting services =="
systemctl --user restart machinespirit-api.service machinespirit-ui.service
sleep 1

echo "== guarded_apply: running selftest =="
set +e
./scripts/selftest.sh
TEST_RC=$?
set -e

if [ $TEST_RC -ne 0 ]; then
  echo "FAIL: selftest failed — restoring backups + restarting"
  systemctl --user stop machinespirit-api.service machinespirit-ui.service || true
  cp -a "$BKDIR/ms_api.py" ms_api.py
  cp -a "$BKDIR/ms_ui.py"  ms_ui.py
  [ -f "$BKDIR/brain.py" ] && cp -a "$BKDIR/brain.py" brain.py || true
  systemctl --user restart machinespirit-api.service machinespirit-ui.service
  sleep 1
  echo "RESTORED. Check: $BKDIR"
  exit 1
fi

echo "PASS: guarded apply + selftest succeeded"
