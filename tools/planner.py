#!/usr/bin/env python3
from __future__ import annotations
import sys
import os
import json

# Ensure project root is on sys.path when running as a script
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from storage.memory import (  # noqa: E402
    queue_learning,
    list_learning_queue,
    pop_learning_queue,
    add_knowledge,
    append_note,
)

USAGE = """\
Usage:
  python3 tools/planner.py add "TOPIC"
  python3 tools/planner.py list
  python3 tools/planner.py next
  python3 tools/planner.py clear
  python3 tools/planner.py learn-now
"""


def cmd_add(args):
    topic = " ".join(args).strip()
    if not topic:
        print("ERR: missing topic")
        sys.exit(2)
    queue_learning(topic)
    print(f"[planner] queued: {topic}")


def cmd_list(_args):
    q = list_learning_queue()
    print(json.dumps(q, indent=2))


def cmd_next(_args):
    item = pop_learning_queue()
    if not item:
        print("EMPTY")
    else:
        print(json.dumps(item, indent=2))


def cmd_clear(_args):
    changed = 0
    while True:
        item = pop_learning_queue()
        if not item:
            break
        changed += 1
    print(f"[planner] cleared {changed} item(s)")


def cmd_learn_now(_args):
    item = pop_learning_queue()
    if not item:
        print("EMPTY")
        return
    topic = (item.get("topic") or "").strip()
    if not topic:
        print("EMPTY")
        return
    try:
        import learning_shim  # must exist in your repo
        print(f"[planner] learning-now: {topic}")
        if hasattr(learning_shim, "search_and_learn"):
            res = learning_shim.search_and_learn(topic)
        elif hasattr(learning_shim, "learn"):
            res = learning_shim.learn(topic)
        else:
            raise RuntimeError("learning_shim has no learn/search_and_learn")

        # If shim returns (summary, sources) capture it
        if isinstance(res, tuple) and len(res) >= 1:
            summary = res[0] or f"Learned topic: {topic}"
            sources = res[1] if len(res) > 1 else []
            add_knowledge(topic, summary, sources or [])
        else:
            add_knowledge(topic, f"Learn completed for: {topic}", [])

        print("[planner] learn complete")
    except Exception as e:
        append_note(f"LEARN-NOW FAILED for topic: {topic}. Error: {e}")
        print(f"[planner] learn failed: {e}")


def main():
    if len(sys.argv) < 2:
        print(USAGE)
        sys.exit(2)
    cmd = sys.argv[1].lower()
    args = sys.argv[2:]
    if cmd == "add":
        cmd_add(args)
    elif cmd == "list":
        cmd_list(args)
    elif cmd == "next":
        cmd_next(args)
    elif cmd == "clear":
        cmd_clear(args)
    elif cmd in ("learn-now", "learnnow"):
        cmd_learn_now(args)
    else:
        print(USAGE)
        sys.exit(2)


if __name__ == "__main__":
    main()
