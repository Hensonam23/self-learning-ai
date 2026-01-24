#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

STAMP="$(date +%Y%m%d-%H%M%S)"
BK="data/backups/upgrade_autopropose_v2/$STAMP"
mkdir -p "$BK/scripts"

echo "== upgrade_autopropose_v2 =="
echo "repo: $REPO_DIR"
echo "backup: $BK"

# Backups
[ -f scripts/new_proposal.sh ] && cp -a scripts/new_proposal.sh "$BK/scripts/new_proposal.sh" || true
[ -f scripts/auto_propose.py ] && cp -a scripts/auto_propose.py "$BK/scripts/auto_propose.py" || true

# ------------------------------------------------------------
# 1) new_proposal.sh (hardened)
# - apply.sh ALWAYS cds to repo root
# - command is embedded via heredoc (no quoting breakage)
# - supports: --shell "<cmd>" and --stdin
# ------------------------------------------------------------
cat > scripts/new_proposal.sh <<'SH'
#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage:"
  echo "  ./scripts/new_proposal.sh \"Title\" --shell '<command string>'"
  echo "  ./scripts/new_proposal.sh \"Title\" --stdin   # reads command from stdin"
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
shift

CMD=""
if [ "$MODE" = "--shell" ]; then
  if [ $# -lt 1 ]; then
    echo "ERROR: --shell requires a command string"
    usage
    exit 2
  fi
  CMD="$1"
elif [ "$MODE" = "--stdin" ]; then
  CMD="$(cat)"
else
  echo "ERROR: unknown mode: $MODE"
  usage
  exit 2
fi

if [ -z "${CMD//[[:space:]]/}" ]; then
  echo "ERROR: empty command"
  exit 2
fi

mkdir -p proposals

stamp="$(date +%Y%m%d-%H%M%S)"
slug="$(echo "$TITLE" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//;')"
[ -n "$slug" ] || slug="proposal"

P="proposals/${stamp}_${slug}"
mkdir -p "$P"

# status + metadata
echo "pending" > "$P/status.txt"
cat > "$P/meta.json" <<META
{
  "title": "$(python3 - <<PY
import json
print(json.dumps($TITLE)[1:-1])
PY
)",
  "created": "$stamp",
  "slug": "$slug"
}
META

# write apply.sh safely (repo-root always)
{
  cat <<'APPLY_HEAD'
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

CMD="$(cat <<'CMD_EOF'
APPLY_HEAD

  # literal command body
  printf '%s\n' "$CMD"

  cat <<'APPLY_TAIL'
CMD_EOF
)"

echo "== proposal: running guarded_apply =="
./scripts/guarded_apply.sh --shell "$CMD"
APPLY_TAIL
} > "$P/apply.sh"

chmod +x "$P/apply.sh"

echo "OK: created proposal folder:"
echo "  $P"
SH
chmod +x scripts/new_proposal.sh

# ------------------------------------------------------------
# 2) auto_propose.py (V2)
# - never writes apply.sh directly
# - calls scripts/new_proposal.sh to generate proposals
# - uses repo-root safe commands
# ------------------------------------------------------------
cat > scripts/auto_propose.py <<'PY'
#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from datetime import datetime

REPO_DIR = Path(__file__).resolve().parents[1]
PROPOSALS_DIR = REPO_DIR / "proposals"
RESEARCH_QUEUE = REPO_DIR / "data" / "research_queue.json"


def _pending_proposals_exist() -> bool:
    if not PROPOSALS_DIR.exists():
        return False
    for d in sorted(PROPOSALS_DIR.glob("*"), reverse=True):
        if not d.is_dir():
            continue
        st = (d / "status.txt")
        if not st.exists():
            return True
        s = st.read_text(encoding="utf-8", errors="replace").strip().lower()
        if s in ("", "pending"):
            return True
    return False


def _count_research_pending() -> int:
    try:
        if not RESEARCH_QUEUE.exists():
            return 0
        raw = json.loads(RESEARCH_QUEUE.read_text(encoding="utf-8", errors="replace") or "[]")
        if not isinstance(raw, list):
            return 0
        return sum(1 for x in raw if isinstance(x, dict) and x.get("status") == "pending")
    except Exception:
        return 0


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, cwd=str(REPO_DIR), check=True)


def main() -> int:
    PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)

    if _pending_proposals_exist():
        print("INFO: pending proposal already exists; skipping auto-propose.")
        return 0

    pending = _count_research_pending()

    # Simple policy (safe + deterministic):
    # If research pending exists, propose curiosity learning.
    # Otherwise propose a light curiosity run anyway.
    if pending > 0:
        n = 5
        title = f"Maintenance: curiosity n={n} (pending research: {pending})"
    else:
        n = 3
        title = f"Maintenance: curiosity n={n}"

    # Run from repo root, and use system python for timer safety
    cmd = f'cd "{REPO_DIR}" && /usr/bin/python3 brain.py --curiosity --n {n}'

    print("INFO: creating proposal:", title)
    _run(["bash", "scripts/new_proposal.sh", title, "--shell", cmd])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY
chmod +x scripts/auto_propose.py

# quick syntax checks on the scripts we touched
bash -n scripts/new_proposal.sh
python3 -m py_compile scripts/auto_propose.py

echo "OK: upgrade_autopropose_v2 complete"
