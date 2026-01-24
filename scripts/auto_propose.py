#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent

def norm_status(s: str) -> str:
    s = (s or "").strip().lower()
    return s if s else "pending"

def pending_proposals_exist(pdir: Path) -> bool:
    if not pdir.exists():
        return False
    for d in sorted(pdir.glob("*"), reverse=True):
        if not d.is_dir():
            continue
        st = "pending"
        st_path = d / "status.txt"
        if st_path.exists():
            st = norm_status(st_path.read_text(encoding="utf-8", errors="replace"))
        if st == "pending":
            return True
    return False

def slugify(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "proposal"

def count_pending_research(repo: Path) -> int:
    q = repo / "data" / "research_queue.json"
    if not q.exists():
        return 0
    try:
        arr = json.loads(q.read_text(encoding="utf-8", errors="replace") or "[]")
        if not isinstance(arr, list):
            return 0
        return sum(1 for x in arr if isinstance(x, dict) and x.get("status") == "pending")
    except Exception:
        return 0

def write_proposal(repo: Path, title: str, cmd_lines: list[str]) -> Path:
    pdir = repo / "proposals"
    pdir.mkdir(parents=True, exist_ok=True)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder = f"{stamp}_{slugify(title)}"
    p = pdir / folder
    p.mkdir(parents=True, exist_ok=False)

    (p / "status.txt").write_text("pending\n", encoding="utf-8")

    # command script
    cmd = "\n".join([
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f'cd "{repo}"',
        "",
        *cmd_lines,
        "",
    ])
    (p / "cmd.sh").write_text(cmd, encoding="utf-8")
    os.chmod(p / "cmd.sh", 0o755)

    # apply wrapper (runs guarded_apply using the cmd file)
    apply = "\n".join([
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f'cd "{repo}"',
        "",
        'echo "== proposal: running guarded_apply =="',
        f'./scripts/guarded_apply.sh -- bash "{p / "cmd.sh"}"',
        "",
    ])
    (p / "apply.sh").write_text(apply, encoding="utf-8")
    os.chmod(p / "apply.sh", 0o755)

    return p

def main() -> int:
    repo = repo_root()
    proposals_dir = repo / "proposals"

    # Only skip if a REAL pending proposal exists
    if pending_proposals_exist(proposals_dir):
        print("INFO: pending proposal already exists; skipping auto-propose.")
        return 0

    pending = count_pending_research(repo)

    # default maintenance action: run curiosity a small amount
    n = 5
    title = f"Maintenance: curiosity n={n} (pending research: {pending})"

    print(f"INFO: creating proposal: {title}")
    p = write_proposal(
        repo,
        title,
        cmd_lines=[
            f'/usr/bin/python3 "{repo / "brain.py"}" --curiosity --n {n}',
        ],
    )
    print(f"OK: created proposal: {p}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
