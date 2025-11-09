#!/usr/bin/env python3
from __future__ import annotations

import os
import time
from typing import Optional

# ensure project root on path when run as a script
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from web_learning import fetch_best_summary
try:
    from storage.memory import pop_learning_queue, add_knowledge, append_note  # type: ignore
except Exception:
    def pop_learning_queue():
        return None
    def add_knowledge(topic, summary, sources=None, meta=None):
        print("[add_knowledge missing]", topic)
    def append_note(text, tags=None):
        print("[append_note missing] ->", text)

def _utc_now():
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def learn_one() -> bool:
    item = pop_learning_queue()
    if not item:
        return False
    topic = (item.get("topic") or "").strip()
    if not topic:
        return True
    summary, src = fetch_best_summary(topic)
    if summary:
        add_knowledge(topic=topic, summary=summary, sources=[src] if src else [],
                      meta={"kind": "night_learn", "ts": _utc_now(), "source": src or ""})
        append_note(f"[{_utc_now()}] learned: {topic}", tags=["learn", "ok"])
    else:
        append_note(f"[{_utc_now()}] learn failed: {topic}", tags=["learn", "error"])
    time.sleep(1.2)  # be gentle
    return True

def main():
    worked = False
    for _ in range(64):  # process up to 64 items/night
        if not learn_one():
            break
        worked = True
    if not worked:
        append_note(f"[{_utc_now()}] night learner: nothing to do", tags=["learn", "idle"])

if __name__ == "__main__":
    main()
