#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ $# -lt 2 ]; then
  echo "Usage:"
  echo "  ./scripts/new_proposal.sh \"Title\" --shell '<command string>'"
  echo "Example:"
  echo "  ./scripts/new_proposal.sh \"UI: add override endpoint\" --shell 'python3 scripts/patch_ui_override.py'"
  exit 2
fi

TITLE="$1"
MODE="$2"
shift 2

if [ "$MODE" != "--shell" ]; then
  echo "ERROR: only --shell is supported right now"
  exit 2
fi

CMD="${1:-}"
if [ -z "$CMD" ]; then
  echo "ERROR: missing shell command string"
  exit 2
fi

# slugify title (simple + safe)
SLUG="$(echo "$TITLE" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//')"
STAMP="$(date +%Y%m%d-%H%M%S)"
DIR="proposals/${STAMP}_${SLUG}"

mkdir -p "$DIR"

cat > "$DIR/status.json" <<JSON
{
  "status": "pending",
  "created_at": "$(date -Iseconds)",
  "title": $(python3 - <<PY
import json
print(json.dumps("$TITLE"))
PY
),
  "slug": "$SLUG"
}
JSON

cat > "$DIR/README.md" <<EOF2
# Proposal: $TITLE

Created: $(date -Iseconds)  
Status: **pending**

## Goal
(Write what this change is supposed to improve.)

## Why
(What problem did we see? logs? bad behavior? etc.)

## Risk / Safety
- This proposal must pass: guarded_apply + selftest
- If selftest fails, it auto-restores

## Apply
Run:
\`\`\`bash
./scripts/apply_proposal.sh "$DIR"
\`\`\`

## Notes
(Add anything helpful.)
EOF2

cat > "$DIR/apply.sh" <<EOF3
#!/usr/bin/env bash
set -euo pipefail
cd "\$(dirname "\$0")/.."

# Proposal apply command:
$CMD
EOF3
chmod +x "$DIR/apply.sh"

echo "OK: created proposal folder:"
echo "  $DIR"
