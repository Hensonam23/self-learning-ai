#!/usr/bin/env python3

"""
Evolution loop for the Machine Spirit.

What this does each pass:

1. Reads data/chatlog.json and looks for entries where needs_research == True.
   - Queues those questions as research topics (if not already queued).

2. Adds built-in self-study topics (IT, English, Warhammer, AI, humor)
   into the research queue (once each), so the Machine Spirit can learn
   even if you are not actively chatting.

3. Runs the research worker to process ALL pending research tasks:
      - research_worker.py will:
          * call the web answer engine for topics/URLs
          * store summaries in data/research_notes.json
          * store topic summaries in structured memory
            (which also updates data/local_knowledge.json)

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
from memory_manager import normalize_question
from research_worker import run_worker as run_research_worker


CHATLOG_PATH = "data/chatlog.json"

# ---------------------------------------------------------------------------
# Built-in self-study topics.
#
# These are questions/topics the Machine Spirit will research on its own,
# even if you never asked them directly. They are intentionally broad and
# cover IT, English, Warhammer, AI, and humor.
# ---------------------------------------------------------------------------

SELF_STUDY_TOPICS: List[str] = [
    # --- IT / networking / systems ---
    "Explain the OSI model in simple words.",
    "What is the difference between TCP and UDP, and when would you use each?",
    "Explain what an IP address and subnet mask are, in simple terms.",
    "How does DNS work and why is it important on a network?",
    "What is the difference between a switch and a router?",
    "Explain VLANs in simple words.",
    "What is a VPN and what is it used for?",
    "Explain the basics of firewalls and how they protect a network.",
    "What is virtualization and how is it different from containers?",
    "Compare virtual machines and Docker containers in simple words.",
    "What are common ways to optimize a Windows PC for gaming performance?",
    "Explain CPU, GPU, and RAM roles in gaming performance.",
    "What is RAID and what are the common RAID levels used for?",
    "Explain what a load balancer does in a web application setup.",

    # --- English / writing / grammar ---
    "Explain the difference between their, there, and they're with examples.",
    "What is a run-on sentence and how can you fix it?",
    "Explain subject-verb agreement in simple words.",
    "When should I use a comma in English sentences?",
    "Explain the difference between active voice and passive voice.",
    "How can I make my writing clearer and easier to read?",
    "What are some common grammar mistakes English learners make?",
    "How can I improve my vocabulary effectively?",

    # --- AI / machine learning ---
    "Explain the difference between artificial intelligence, machine learning, and deep learning.",
    "What is a neural network in simple terms?",
    "What are large language models and how do they work at a high level?",
    "What is overfitting in machine learning and how can it be reduced?",
    "What are some ethical concerns around AI systems?",
    "Explain supervised, unsupervised, and reinforcement learning in simple words.",
    "What is a training dataset and why does its quality matter?",

    # --- Warhammer 40k / lore ---
    "Give an overview of the Imperium of Man in Warhammer 40k.",
    "Explain who the Adeptus Mechanicus are and what they believe.",
    "Who are the Space Marines in Warhammer 40k and what is their role?",
    "Summarize the Horus Heresy in simple terms.",
    "Explain the relationship between the Machine Spirit and technology in Warhammer 40k.",
    "What is life like for an ordinary human citizen in the Imperium of Man?",

    # --- Humor / interaction style ---
    "Explain what makes a joke funny in simple psychological terms.",
    "What is the difference between sarcasm and playful teasing?",
    "How can someone be funny without being mean to other people?",
    "What is self-deprecating humor and when is it useful?",
    "Explain comedic timing in simple words.",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _enqueue_chatlog_topics(
    mgr: ResearchManager,
    chatlog: List[Dict[str, Any]],
    existing_topic_keys: List[str],
) -> int:
    """
    From the chatlog, enqueue topics where needs_research == True.
    Returns how many were added.
    """
    added = 0
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
            added += 1

        except Exception as e:
            print(f"[evolve] Skipping one chatlog entry due to error: {e!r}")

    return added


def _enqueue_self_study_topics(
    mgr: ResearchManager,
    existing_topic_keys: List[str],
) -> int:
    """
    Enqueue built-in self-study topics if they are not already in the queue.
    Returns how many were added.
    """
    added = 0
    for topic in SELF_STUDY_TOPICS:
        norm_q = normalize_question(topic)
        if not norm_q:
            continue
        if norm_q in existing_topic_keys:
            continue

        mgr.queue_topic(
            user_text=topic,
            reason="self_study",
            channel="self",
        )
        existing_topic_keys.append(norm_q)
        added += 1

    return added


# ---------------------------------------------------------------------------
# Evolution pass
# ---------------------------------------------------------------------------

def evolution_pass() -> None:
    """
    Run a single evolution cycle:

    - Scan chatlog for entries that need research.
    - Add built-in self-study topics.
    - Queue missing topics into research_queue.json.
    - Run the research worker to process all pending tasks.
    """
    print("[evolve] Starting evolution pass...")

    mgr = ResearchManager()
    queue = mgr.get_queue()
    chatlog = _load_chatlog(CHATLOG_PATH)

    existing_topic_keys = _collect_existing_topic_keys(queue)

    # Step 1: enqueue missing research topics from chatlog
    added_from_chat = _enqueue_chatlog_topics(mgr, chatlog, existing_topic_keys)
    if added_from_chat:
        print(f"[evolve] Queued {added_from_chat} new research topic(s) from chatlog.")
    else:
        print("[evolve] No new research topics to queue from chatlog.")

    # Step 2: enqueue built-in self-study topics
    added_self = _enqueue_self_study_topics(mgr, existing_topic_keys)
    if added_self:
        print(f"[evolve] Queued {added_self} self-study topic(s).")
    else:
        print("[evolve] No new self-study topics to queue (all already queued).")

    # Step 3: run the research worker to process all pending tasks
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
