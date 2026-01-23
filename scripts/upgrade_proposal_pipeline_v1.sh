#!/usr/bin/env bash
set -euo pipefail

# Always operate from repo root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

STAMP="$(date +%Y%m%d-%H%M%S)"
BK="data/backups/upgrade_pipeline/$STAMP"
mkdir -p "$BK"

echo "== upgrade_proposal_pipeline_v1 =="
echo "repo: $REPO_DIR"
echo "backup: $BK"

# backups
for f in scripts/guarded_apply.sh scripts/apply_proposal.sh scripts/apply_latest_proposal.sh .gitignore; do
  if [ -f "$f" ]; then
    mkdir -p "$BK/$(dirname "$f")"
    cp -a "$f" "$BK/$f"
  fi
done

# ------------------------------------------------------------
# 1) guarded_apply.sh: always run from repo root, fail if upgrade fails
# ------------------------------------------------------------
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

echo "== guarded_apply: repo_dir = $REPO_DIR =="
echo "== guarded_apply: stopping services =="
systemctl --user stop machinespirit-api.service machinespirit-ui.service || true

echo "== guarded_apply: backing up files to $BKDIR =="
for f in ms_api.py ms_ui.py brain.py .gitignore scripts/selftest.sh scripts/guarded_apply.sh scripts/auto_propose.py scripts/reflect.py; do
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
  for f in ms_api.py ms_ui.py brain.py .gitignore scripts/selftest.sh scripts/guarded_apply.sh scripts/auto_propose.py scripts/reflect.py; do
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
  for f in ms_api.py ms_ui.py brain.py .gitignore scripts/selftest.sh scripts/guarded_apply.sh scripts/auto_propose.py scripts/reflect.py; do
    if [ -f "$BKDIR/$f" ]; then
      cp -a "$BKDIR/$f" "$f"
    fi
  done
  systemctl --user restart machinespirit-api.service machinespirit-ui.service
  sleep 1
  echo "RESTORED. Check: $BKDIR"
  exit 1
fi

# IMPORTANT: even if we restored and selftest passes, still return failure if upgrade failed
if [ $UPGRADE_RC -ne 0 ]; then
  echo "RESTORED: selftest passed, but upgrade command failed (rc=$UPGRADE_RC)."
  echo "Check: $BKDIR"
  exit 1
fi

echo "PASS: guarded apply + selftest succeeded"
SH
chmod +x scripts/guarded_apply.sh

# ------------------------------------------------------------
# 2) apply_proposal.sh: run apply.sh from repo root (fixes cwd bug)
# ------------------------------------------------------------
cat > scripts/apply_proposal.sh <<'SH'
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

if [ $# -ne 1 ]; then
  echo "Usage: ./scripts/apply_proposal.sh <proposal_dir>"
  exit 2
fi

P="$1"
if [ ! -d "$P" ]; then
  echo "ERROR: proposal dir not found: $P"
  exit 2
fi
if [ ! -f "$P/apply.sh" ]; then
  echo "ERROR: apply.sh not found in: $P"
  exit 2
fi

LOG="$P/apply_$(date +%Y%m%d-%H%M%S).log"
echo "== apply_proposal =="
echo "Proposal: $P"
echo "Log: $LOG"
echo

set +e
( cd "$REPO_DIR" && bash "$P/apply.sh" ) 2>&1 | tee "$LOG"
RC=${PIPESTATUS[0]}
set -e

if [ $RC -ne 0 ]; then
  echo "failed" > "$P/status.txt"
  echo
  echo "FAIL: proposal apply failed (rc=$RC)"
  exit $RC
fi

echo "applied" > "$P/status.txt"
echo
echo "PASS: proposal applied successfully."
SH
chmod +x scripts/apply_proposal.sh

# ------------------------------------------------------------
# 3) apply_latest_proposal.sh: propagate real failures
# ------------------------------------------------------------
cat > scripts/apply_latest_proposal.sh <<'SH'
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

if [ ! -d proposals ]; then
  echo "No proposals/ directory."
  exit 0
fi

pick=""
while IFS= read -r d; do
  [ -d "$d" ] || continue
  st="pending"
  if [ -f "$d/status.txt" ]; then
    st="$(cat "$d/status.txt" | tr -d '\r\n' | tr '[:upper:]' '[:lower:]')"
    [ -n "$st" ] || st="pending"
  fi
  if [ "$st" = "pending" ]; then
    pick="$d"
    break
  fi
done < <(ls -1dt proposals/* 2>/dev/null)

if [ -z "$pick" ]; then
  echo "No pending proposals."
  exit 0
fi

./scripts/apply_proposal.sh "$pick"
SH
chmod +x scripts/apply_latest_proposal.sh

# ------------------------------------------------------------
# 4) proposals should never be committed
# ------------------------------------------------------------
if [ ! -f .gitignore ]; then
  touch .gitignore
fi
if ! grep -qE '^proposals/$' .gitignore; then
  {
    echo ""
    echo "# runtime proposals (do not commit)"
    echo "proposals/"
  } >> .gitignore
fi

# sanity
python3 -m py_compile ms_api.py ms_ui.py brain.py scripts/auto_propose.py scripts/reflect.py

echo "OK: upgrade_proposal_pipeline_v1 complete"
