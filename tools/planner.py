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
        # Use answer_engine directly for learning
        from answer_engine import respond
        print(f"[planner] learning-now: {topic}")
        
        # Generate a learning prompt for the topic
        prompt = f"Learn and explain: {topic}"
        result = respond(prompt)
        
        # Store the learned knowledge
        add_knowledge(topic, result or f"Learned topic: {topic}", [])
        
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
