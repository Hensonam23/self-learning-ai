#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

STAMP="$(date +%Y%m%d-%H%M%S)"
BK="data/backups/upgrade_proposal_runner_v3/$STAMP"
mkdir -p "$BK"

echo "== upgrade_proposal_runner_v3 =="
echo "repo:   $REPO_DIR"
echo "backup: $BK"

# backups
for f in scripts/new_proposal.sh scripts/apply_proposal.sh scripts/apply_latest_proposal.sh; do
  if [ -f "$f" ]; then
    mkdir -p "$BK/$(dirname "$f")"
    cp -a "$f" "$BK/$f"
  fi
done

# ------------------------------------------------------------
# 1) Rewrite scripts/new_proposal.sh (no embedded python quoting)
#    It writes two files into each proposal:
#      - cmd.sh   (the actual command)
#      - apply.sh (runs guarded_apply from repo root and executes cmd.sh)
# ------------------------------------------------------------
cat > scripts/new_proposal.sh <<'SH'
#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage:"
  echo "  ./scripts/new_proposal.sh \"Title\" --shell '<shell string>'"
  echo "  ./scripts/new_proposal.sh \"Title\" -- <command> [args...]"
}

if [ $# -lt 2 ]; then
  usage
  exit 2
fi

TITLE="$1"
shift

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

STAMP="$(date +%Y%m%d-%H%M%S)"

# slugify title safely (no shell to python injection)
SLUG="$(python3 - <<PY
import re
t = """$TITLE""".strip().lower()
t = re.sub(r'[^a-z0-9]+', '-', t).strip('-')
print(t or "proposal")
PY
)"

P="proposals/${STAMP}_${SLUG}"
mkdir -p "$P"
echo "pending" > "$P/status.txt"

# write cmd.sh
cat > "$P/cmd.sh" <<'CMD'
#!/usr/bin/env bash
set -euo pipefail
CMD
chmod +x "$P/cmd.sh"

if [ "${1:-}" = "--shell" ]; then
  shift
  if [ $# -lt 1 ]; then
    echo "ERROR: --shell requires a command string"
    exit 2
  fi
  # put the shell content inside cmd.sh exactly
  printf "%s\n" "$1" >> "$P/cmd.sh"
elif [ "${1:-}" = "--" ]; then
  shift
  if [ $# -lt 1 ]; then
    echo "ERROR: -- requires a command"
    exit 2
  fi
  # execute args safely in cmd.sh
  {
    printf 'exec'
    for a in "$@"; do
      printf ' %q' "$a"
    done
    printf '\n'
  } >> "$P/cmd.sh"
else
  echo "ERROR: expected --shell or --"
  usage
  exit 2
fi

# write apply.sh (cwd proof)
cat > "$P/apply.sh" <<'APPLY'
#!/usr/bin/env bash
set -euo pipefail

# proposal dir is this file's directory
PROP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$PROP_DIR/../.." && pwd)"
cd "$REPO_DIR"

echo "== proposal: repo = $REPO_DIR =="
echo "== proposal: cmd  = $PROP_DIR/cmd.sh =="

# run via guarded_apply from repo root, execute the cmd.sh file
./scripts/guarded_apply.sh -- bash "$PROP_DIR/cmd.sh"
APPLY
chmod +x "$P/apply.sh"

# save title
printf "%s\n" "$TITLE" > "$P/title.txt"

echo "OK: created proposal folder:"
echo "  $P"
SH
chmod +x scripts/new_proposal.sh

# ------------------------------------------------------------
# 2) Patch apply_proposal.sh to be extra tolerant
#    If an old proposal apply.sh still expects ./scripts/guarded_apply.sh
#    from inside the proposal folder, we give it a scripts symlink.
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

# Compatibility shim for old proposals:
# if the proposal cd's into itself and tries ./scripts/guarded_apply.sh, make it exist
ln -sfn "$(cd scripts && pwd)" "$P/scripts" 2>/dev/null || true

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

echo "OK: upgrade_proposal_runner_v3 complete"
