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
