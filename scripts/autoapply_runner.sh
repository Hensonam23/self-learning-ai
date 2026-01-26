#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== autoapply_runner =="
echo "when: $(date -Is)"
echo "repo: $(pwd)"

# Maintenance lock so watchdog (and other stuff) can skip while we apply
mkdir -p data/runtime
LOCK_FILE="data/runtime/maintenance.lock"
echo "$(date -Is) pid=$$ autoapply" > "$LOCK_FILE"
_cleanup_lock(){ rm -f "$LOCK_FILE" || true; }
trap _cleanup_lock EXIT

# Run apply and capture output so we can tell if something actually applied
TMPLOG="$(mktemp)"
set +e
./scripts/apply_latest_proposal.sh 2>&1 | tee "$TMPLOG"
rc=${PIPESTATUS[0]}
set -e

if [ $rc -ne 0 ]; then
  echo "AUTOAPPLY: FAIL (apply_latest_proposal rc=$rc)"
  exit $rc
fi

if ! grep -q "PASS: proposal applied successfully" "$TMPLOG"; then
  echo "AUTOAPPLY: no proposal applied (or nothing pending) -> done"
  exit 0
fi

# Extract proposal folder name (best effort)
proposal="$(grep -m1 '^Proposal: proposals/' "$TMPLOG" | sed 's/^Proposal: //')"
[ -n "$proposal" ] || proposal="proposals/<unknown>"

echo "AUTOAPPLY: proposal applied OK: $proposal"
echo "AUTOAPPLY: checking git changes (tracked only) ..."

# If git identity missing, set repo-local defaults (safe)
if ! git config user.name >/dev/null; then
  git config user.name "MachineSpirit"
fi
if ! git config user.email >/dev/null; then
  git config user.email "machinespirit@localhost"
fi

# Only commit tracked modifications (NO new untracked files)
if git diff --quiet && git diff --cached --quiet; then
  echo "AUTOAPPLY: no tracked changes to commit -> done"
  exit 0
fi

git add -u

# If staging still empty, bail
if git diff --cached --quiet; then
  echo "AUTOAPPLY: nothing staged after git add -u -> done"
  exit 0
fi

msg="auto: apply $(basename "$proposal")"
git commit -m "$msg" || true

# Optional push (OFF by default)
if [ "${MS_AUTOPUSH:-0}" = "1" ]; then
  echo "AUTOAPPLY: autopush enabled -> pushing main"
  git push || true
else
  echo "AUTOAPPLY: autopush disabled (MS_AUTOPUSH=0)"
fi

echo "AUTOAPPLY: done"
