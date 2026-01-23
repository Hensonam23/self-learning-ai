#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage:"
  echo "  ./scripts/guarded_apply.sh -- <command> [args...]"
  echo "  ./scripts/guarded_apply.sh --shell '<shell command string>'"
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

if [ $# -lt 1 ]; then
  usage
  exit 2
fi

# allow: guarded_apply.sh -- <cmd...>
if [ "${1:-}" = "--" ]; then
  shift
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
BKDIR="data/backups/guarded_apply/$STAMP"
mkdir -p "$BKDIR"

echo "== guarded_apply: repo_dir = $REPO_DIR =="
echo "== guarded_apply: stopping services =="
systemctl --user stop machinespirit-api.service machinespirit-ui.service || true

echo "== guarded_apply: backing up files to $BKDIR =="
for f in ms_api.py ms_ui.py brain.py .gitignore scripts/selftest.sh scripts/guarded_apply.sh scripts/auto_propose.py scripts/reflect.py scripts/apply_proposal.sh scripts/apply_latest_proposal.sh scripts/new_proposal.sh; do
  if [ -f "$f" ]; then
    mkdir -p "$(dirname "$BKDIR/$f")"
    cp -a "$f" "$BKDIR/$f"
  fi
done

echo "== guarded_apply: running upgrade command =="
UPGRADE_RC=0
set +e
if [ "${1:-}" = "--shell" ]; then
  shift
  if [ $# -lt 1 ]; then
    echo "ERROR: --shell requires a command string"
    usage
    exit 2
  fi
  bash -lc "$1"
  UPGRADE_RC=$?
else
  "$@"
  UPGRADE_RC=$?
fi
set -e

if [ $UPGRADE_RC -ne 0 ]; then
  echo "FAIL: upgrade command failed (rc=$UPGRADE_RC) — restoring backups"
  systemctl --user stop machinespirit-api.service machinespirit-ui.service || true
  for f in ms_api.py ms_ui.py brain.py .gitignore scripts/selftest.sh scripts/guarded_apply.sh scripts/auto_propose.py scripts/reflect.py scripts/apply_proposal.sh scripts/apply_latest_proposal.sh scripts/new_proposal.sh; do
    if [ -f "$BKDIR/$f" ]; then
      cp -a "$BKDIR/$f" "$f"
    fi
  done
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
  for f in ms_api.py ms_ui.py brain.py .gitignore scripts/selftest.sh scripts/guarded_apply.sh scripts/auto_propose.py scripts/reflect.py scripts/apply_proposal.sh scripts/apply_latest_proposal.sh scripts/new_proposal.sh; do
    if [ -f "$BKDIR/$f" ]; then
      cp -a "$BKDIR/$f" "$f"
    fi
  done
  systemctl --user restart machinespirit-api.service machinespirit-ui.service
  sleep 1
  echo "RESTORED. Check: $BKDIR"
  exit 1
fi

# important: do NOT pretend success if the upgrade failed
if [ $UPGRADE_RC -ne 0 ]; then
  echo "RESTORED: selftest passed, but upgrade command failed (rc=$UPGRADE_RC)."
  echo "Check: $BKDIR"
  exit 1
fi

echo "PASS: guarded apply + selftest succeeded"
