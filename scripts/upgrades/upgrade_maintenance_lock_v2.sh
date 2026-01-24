#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "== upgrade_maintenance_lock_v2 =="

# ----------------------------
# 1) Rewrite scripts/guarded_apply.sh (known-good + lock)
# ----------------------------
cat > scripts/guarded_apply.sh <<'SH'
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

# --- MS_MAINT_LOCK_V2 ---
LOCK_DIR="data/runtime"
LOCK_FILE="$LOCK_DIR/maintenance.lock"
mkdir -p "$LOCK_DIR"
echo "$(date -Is) pid=$$" > "$LOCK_FILE"
_ms_cleanup_lock(){ rm -f "$LOCK_FILE" || true; }
trap _ms_cleanup_lock EXIT

echo "== guarded_apply: repo_dir = $REPO_DIR =="
echo "== guarded_apply: stopping services =="
systemctl --user stop machinespirit-api.service machinespirit-ui.service || true

echo "== guarded_apply: backing up files to $BKDIR =="
FILES=(
  ms_api.py
  ms_ui.py
  brain.py
  .gitignore
  scripts/selftest.sh
  scripts/guarded_apply.sh
  scripts/apply_proposal.sh
  scripts/apply_latest_proposal.sh
  scripts/new_proposal.sh
  scripts/auto_propose.py
  scripts/reflect.py
  scripts/watchdog_check.sh
)
for f in "${FILES[@]}"; do
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
  echo "FAIL: upgrade command failed (rc=$UPGRADE_RC) - restoring backups"
  systemctl --user stop machinespirit-api.service machinespirit-ui.service || true
  for f in "${FILES[@]}"; do
    if [ -f "$BKDIR/$f" ]; then
      cp -a "$BKDIR/$f" "$f"
    fi
  done
  systemctl --user restart machinespirit-api.service machinespirit-ui.service || true
  sleep 1
  ./scripts/selftest.sh || true
  exit $UPGRADE_RC
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
  echo "FAIL: selftest failed - restoring backups + restarting"
  systemctl --user stop machinespirit-api.service machinespirit-ui.service || true
  for f in "${FILES[@]}"; do
    if [ -f "$BKDIR/$f" ]; then
      cp -a "$BKDIR/$f" "$f"
    fi
  done
  systemctl --user restart machinespirit-api.service machinespirit-ui.service
  sleep 1
  ./scripts/selftest.sh || true
  echo "RESTORED. Check: $BKDIR"
  exit 1
fi

echo "PASS: guarded apply + selftest succeeded"
SH

chmod +x scripts/guarded_apply.sh

# ----------------------------
# 2) Add maintenance-lock respect to watchdog_check.sh (only if missing)
# ----------------------------
python3 - <<'PY'
from pathlib import Path

p = Path("scripts/watchdog_check.sh")
txt = p.read_text(encoding="utf-8", errors="replace")
if "MS_MAINT_LOCK_V2" in txt:
    print("OK: watchdog_check.sh already has lock logic")
    raise SystemExit(0)

lines = txt.splitlines(True)
if not lines or not lines[0].startswith("#!"):
    raise SystemExit("ERROR: watchdog_check.sh missing shebang")

block = r'''# --- MS_MAINT_LOCK_V2 ---
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

'''
lines.insert(1, block)
p.write_text("".join(lines), encoding="utf-8")
print("OK: patched watchdog_check.sh with lock logic")
PY

chmod +x scripts/watchdog_check.sh

echo "== sanity checks =="
bash -n scripts/guarded_apply.sh
python3 -m py_compile ms_api.py ms_ui.py brain.py
echo "OK: upgrade_maintenance_lock_v2 complete"
