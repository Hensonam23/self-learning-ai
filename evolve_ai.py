#!/usr/bin/env python3

"""
Evolution loop for the Machine Spirit.

What this does:

1. Reads data/chatlog.json and looks for entries where needs_research == True.
2. Ensures each such question is queued in data/research_queue.json (as a topic).
3. Runs the research worker to process all pending research tasks:
      - research_worker.py will:
          * call the web answer engine for topics/URLs
          * store summaries in data/research_notes.json
          * store topic summaries in data/local_knowledge.json
4. Because TeachabilityManager normalizes and cleans local_knowledge.json on load,
   the next time the brain runs it can answer more questions from its new knowledge.

Usage:

    # Single evolution pass:
    python3 evolve_ai.py

    # Continuous evolution (every 10 minutes) until you stop it:
    python3 evolve_ai.py --loop
"""

import json
import os
import sys
import time
from typing import Any, Dict, List

from research_manager import ResearchManager, RESEARCH_QUEUE_PATH
from teachability_manager import normalize_question
from research_worker import run_worker as run_research_worker


CHATLOG_PATH = "data/chatlog.json"


def _load_chatlog(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, list) else []
    except Exception as e:
        backup = f"{path}.corrupt_{int(time.time())}"
        try:
            os.replace(path, backup)
        except Exception:
            pass
        print(f"[evolve] Failed to load chatlog ({e!r}), continuing with empty log.")
        return []


def _collect_existing_topic_keys(queue: List[Dict[str, Any]]) -> List[str]:
    """
    Build a list of normalized question keys that are already queued as topics.
    """
    keys: List[str] = []
    for entry in queue:
        if entry.get("type") != "topic":
            continue
        q = entry.get("user_text", "")
        if not isinstance(q, str):
            continue
        norm = normalize_question(q)
        if norm and norm not in keys:
            keys.append(norm)
    return keys


def evolution_pass() -> None:
    """
    Run a single evolution cycle:

    - Scan chatlog for entries that need research.
    - Queue missing topics into research_queue.json.
    - Run the research worker to process all pending tasks.
    """
    print("[evolve] Starting evolution pass...")

    mgr = ResearchManager()
    queue = mgr.get_queue()
    chatlog = _load_chatlog(CHATLOG_PATH)

    existing_topic_keys = _collect_existing_topic_keys(queue)

    # Step 1: enqueue missing research topics from chatlog
    added_topics = 0
    for entry in chatlog:
        try:
            needs_research = bool(entry.get("needs_research"))
            if not needs_research:
                continue

            question = entry.get("question")
            if not isinstance(question, str) or not question.strip():
                continue

            norm_q = normalize_question(question)
            if not norm_q:
                continue

            if norm_q in existing_topic_keys:
                # Already queued at some point
                continue

            channel = entry.get("channel", "cli")
            mgr.queue_topic(
                user_text=question,
                reason="evolution_from_chatlog",
                channel=channel,
            )
            existing_topic_keys.append(norm_q)
            added_topics += 1

        except Exception as e:
            print(f"[evolve] Skipping one chatlog entry due to error: {e!r}")

    if added_topics:
        print(f"[evolve] Queued {added_topics} new research topic(s) from chatlog.")
    else:
        print("[evolve] No new research topics to queue from chatlog.")

    # Step 2: run the research worker to process all pending tasks
    print("[evolve] Running research worker...")
    run_research_worker()
    print("[evolve] Evolution pass complete.")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in ("--loop", "-l"):
        print("[evolve] Continuous evolution mode enabled. Ctrl+C to stop.")
        try:
            while True:
                evolution_pass()
                # Sleep 10 minutes between passes
                print("[evolve] Sleeping for 600 seconds before next pass...")
                time.sleep(600)
        except KeyboardInterrupt:
            print("\n[evolve] Stopped by user.")
    else:
        evolution_pass()


if __name__ == "__main__":
    main()
