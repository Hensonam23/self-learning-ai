#!/usr/bin/env python3
"""Auto code updater.

Processes "code" tasks from storage.memory.learning_queue.
Each task must provide:
  {
    "topic": "code",
    "file": "relative/path.py",
    "search": "old text",
    "replace": "new text",
    "commit": "commit message"
  }
The script applies the replacement, runs tests, and commits if they pass.
"""

from __future__ import annotations
import os
import subprocess
import sys
from typing import Dict, Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from storage.memory import (
    list_learning_queue,
    pop_learning_queue,
    append_note,
)


def run_tests() -> tuple[bool, str]:
    """Run pytest, returning (ok, output)."""
    try:
        res = subprocess.run([
            sys.executable,
            "-m",
            "pytest",
        ], capture_output=True, text=True)
    except FileNotFoundError:
        return True, "pytest not installed"
    out = res.stdout + res.stderr
    return res.returncode == 0, out


def apply_patch(item: Dict[str, Any]) -> bool:
    path = item.get("file")
    search = item.get("search")
    replace = item.get("replace")
    commit = item.get("commit") or f"auto update {path}"
    if not path or search is None or replace is None:
        append_note(f"autocode: invalid item {item}")
        return False
    abspath = os.path.join(ROOT, path)
    if not os.path.isfile(abspath):
        append_note(f"autocode: missing {path}")
        return False
    with open(abspath, "r", encoding="utf-8") as fh:
        original = fh.read()
    if search not in original:
        append_note(f"autocode: search text not found in {path}")
        return False
    updated = original.replace(search, replace)
    with open(abspath, "w", encoding="utf-8") as fh:
        fh.write(updated)
    ok, output = run_tests()
    if ok:
        subprocess.run(["git", "add", path], check=True)
        subprocess.run(["git", "commit", "-m", commit], check=True)
        append_note(f"autocode: committed {path} -> {commit}")
        return True
    with open(abspath, "w", encoding="utf-8") as fh:
        fh.write(original)
    append_note(f"autocode: tests failed for {path}\n{output}")
    return False


def main() -> None:
    items = list_learning_queue()
    for _ in range(len(items)):
        item = pop_learning_queue()
        if not item:
            break
        if item.get("topic") != "code":
            continue
        apply_patch(item)


if __name__ == "__main__":
    main()
