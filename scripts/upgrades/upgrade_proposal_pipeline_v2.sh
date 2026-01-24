#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

STAMP="$(date +%Y%m%d-%H%M%S)"
BK="data/backups/upgrade_pipeline_v2/$STAMP"
mkdir -p "$BK/scripts"

echo "== upgrade_proposal_pipeline_v2 =="
echo "repo:   $REPO_DIR"
echo "backup: $BK"

# backups
for f in scripts/list_proposals.sh scripts/apply_latest_proposal.sh scripts/auto_propose.py; do
  if [ -f "$f" ]; then
    mkdir -p "$BK/$(dirname "$f")"
    cp -a "$f" "$BK/$f"
  fi
done

# ------------------------------------------------------------
# list_proposals.sh (ignore proposals/_archive and show unknown)
# ------------------------------------------------------------
cat > scripts/list_proposals.sh <<'SH'
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

echo "== Proposals =="

if [ ! -d proposals ]; then
  echo "(none)"
  exit 0
fi

# list only top-level proposal dirs (ignore underscore dirs like _archive)
for d in $(find proposals -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -r); do
  [[ "$d" == _* ]] && continue
  P="proposals/$d"
  st="unknown"
  if [ -f "$P/status.txt" ]; then
    st="$(tr -d '\r\n' < "$P/status.txt" | tr '[:upper:]' '[:lower:]')"
    [ -n "$st" ] || st="unknown"
  fi
  echo "$d  [$st]"
done
SH
chmod +x scripts/list_proposals.sh

# ------------------------------------------------------------
# apply_latest_proposal.sh
# - newest by NAME (timestamp prefix), not mtime
# - only applies status == pending
# - ignores proposals/_archive
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

# sort by name desc (timestamp folders sort correctly)
while IFS= read -r name; do
  [[ "$name" == _* ]] && continue
  P="proposals/$name"
  [ -d "$P" ] || continue

  # MUST have a status file to be considered pending
  if [ ! -f "$P/status.txt" ]; then
    continue
  fi

  st="$(tr -d '\r\n' < "$P/status.txt" | tr '[:upper:]' '[:lower:]')"
  [ -n "$st" ] || st="unknown"

  if [ "$st" = "pending" ]; then
    pick="$P"
    break
  fi
done < <(find proposals -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -r)

if [ -z "$pick" ]; then
  echo "No pending proposals."
  exit 0
fi

./scripts/apply_proposal.sh "$pick"
SH
chmod +x scripts/apply_latest_proposal.sh

# ------------------------------------------------------------
# auto_propose.py tweak:
# - only blocks when status == pending
# - ignores unknown + ignores _archive
# ------------------------------------------------------------
python3 - <<'PY'
from pathlib import Path
import re

p = Path("scripts/auto_propose.py")
txt = p.read_text(encoding="utf-8", errors="replace")

# Replace the pending-proposal detector with a strict version
pat = r"def _pending_proposals_exist\(\)\s*->\s*bool:\s*(?:.|\n)*?\n\n"
m = re.search(pat, txt)
if not m:
    raise SystemExit("ERROR: could not locate _pending_proposals_exist() in scripts/auto_propose.py")

new = """def _pending_proposals_exist() -> bool:
    if not PROPOSALS_DIR.exists():
        return False
    # only block on explicit 'pending' status
    for d in sorted(PROPOSALS_DIR.glob("*"), reverse=True):
        if not d.is_dir():
            continue
        if d.name.startswith("_"):
            continue
        st = d / "status.txt"
        if not st.exists():
            # legacy/unknown proposals should NOT block auto-propose
            continue
        s = st.read_text(encoding="utf-8", errors="replace").strip().lower()
        if s == "pending":
            return True
    return False

"""

txt2 = txt[:m.start()] + new + txt[m.end():]
p.write_text(txt2, encoding="utf-8")
print("OK: patched auto_propose.py pending detection to strict pending-only")
PY

python3 -m py_compile scripts/auto_propose.py
echo "OK: upgrade_proposal_pipeline_v2 complete"
