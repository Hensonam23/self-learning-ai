#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage:"
  echo '  ./scripts/new_proposal.sh "Title" --shell "<shell command string>"'
  echo "  ./scripts/new_proposal.sh \"Title\" -- <command> [args...]"
}

slugify() {
  echo "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+|-+$//g; s/-+/-/g'
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

if [ $# -lt 2 ]; then
  usage
  exit 2
fi

TITLE="$1"
shift

MODE="$1"
shift || true

STAMP="$(date +%Y%m%d-%H%M%S)"
SLUG="$(slugify "$TITLE")"
[ -n "$SLUG" ] || SLUG="proposal"
P="proposals/${STAMP}_${SLUG}"

mkdir -p "$P"
echo "pending" > "$P/status.txt"
printf '%s\n' "$TITLE" > "$P/title.txt"
date -Is > "$P/created.txt"

PAYLOAD="$P/payload.sh"
APPLY="$P/apply.sh"

# Build payload.sh (the actual upgrade code)
{
  echo '#!/usr/bin/env bash'
  echo 'set -euo pipefail'
  echo
  if [ "$MODE" = "--shell" ]; then
    if [ $# -lt 1 ]; then
      echo 'echo "ERROR: --shell requires a command string" >&2'
      echo 'exit 2'
    else
      # Write the exact shell snippet into the payload
      echo "$1"
    fi
  elif [ "$MODE" = "--" ]; then
    if [ $# -lt 1 ]; then
      echo 'echo "ERROR: -- requires a command" >&2'
      echo 'exit 2'
    else
      printf 'exec %q' "$1"
      shift
      for a in "$@"; do
        printf ' %q' "$a"
      done
      echo
    fi
  else
    echo 'echo "ERROR: expected --shell or --" >&2'
    echo 'exit 2'
  fi
} > "$PAYLOAD"
chmod +x "$PAYLOAD"

# Build apply.sh (always runs from repo root, never proposal cwd)
cat > "$APPLY" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_DIR"

PAYLOAD="$SCRIPT_DIR/payload.sh"
if [ ! -f "$PAYLOAD" ]; then
  echo "ERROR: missing payload: $PAYLOAD" >&2
  exit 2
fi

# run upgrade guarded, without fragile quoting
exec ./scripts/guarded_apply.sh -- bash "$PAYLOAD"
SH

chmod +x "$APPLY"

echo "OK: created proposal folder:"
echo "  $P"
