#!/usr/bin/env python3

"""
Research worker for the Machine Spirit.

This script should be run manually, for example:

    python3 research_worker.py

It will:

- Load data/research_queue.json
- For each entry with status == "pending":
    - If type == "topic": ask the answer_engine to research and explain it.
    - If type == "url": ask the answer_engine to scan/summarize the URL.
- Store results in data/research_notes.json.
- For topics, also store a normalized Q&A entry into data/local_knowledge.json.
- Mark processed entries as status == "done".

This lets the Machine Spirit gradually become smarter between sessions.
"""

import json
import os
import time
from typing import Any, Dict, List

from research_manager import ResearchManager, RESEARCH_QUEUE_PATH

try:
    # Use the same answer engine the web/chat side uses
    from answer_engine import respond as web_respond  # type: ignore
except Exception:
    def web_respond(text: str) -> str:
        return "Research worker could not load the web answer engine. No external research was performed."


LOCAL_KNOWLEDGE_PATH = "data/local_knowledge.json"
RESEARCH_NOTES_PATH = "data/research_notes.json"


# ---- basic file helpers ----------------------------------------------------

def _ensure_dir(path: str) -> None:
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)


def _load_json_dict(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        backup = f"{path}.corrupt_{int(time.time())}"
        try:
            os.replace(path, backup)
        except Exception:
            pass
        return {}


def _save_json_dict(path: str, data: Dict[str, Any]) -> None:
    _ensure_dir(path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


# ---- normalization ---------------------------------------------------------

def _normalize_question(text: str) -> str:
    """
    Same idea as TeachabilityManager._normalize:

    - strip whitespace
    - lowercase
    - strip leading '>' markers
    - collapse internal spaces
    """
    t = text.strip().lower()
    while t.startswith(">"):
        t = t[1:].lstrip()
    return " ".join(t.split())


# ---- processing functions --------------------------------------------------

def _process_topic(entry: Dict[str, Any]) -> Dict[str, Any]:
    user_text = entry.get("user_text", "").strip()
    if not user_text:
        return {
            "ok": False,
            "reason": "empty_user_text",
            "summary": "No user_text found for this topic entry.",
        }

    prompt = (
        "You are the web/research half of a local assistant called the Machine Spirit.\n"
        "The user previously asked this question or raised this topic:\n\n"
        f"\"{user_text}\"\n\n"
        "Using your full external knowledge and tools, produce a clear, accurate explanation "
        "that can be stored as reference knowledge. Use plain text (no markdown), keep it "
        "compact but understandable. Focus on what the Machine Spirit should remember "
        "to answer this correctly in the future."
    )

    try:
        summary = web_respond(prompt)
    except Exception as e:
        summary = f"Research error while calling web_respond: {e!r}"

    return {
        "ok": True,
        "summary": summary,
        "source_question": user_text,
    }


def _process_url(entry: Dict[str, Any]) -> Dict[str, Any]:
    url = entry.get("url", "").strip()
    if not url:
        return {
            "ok": False,
            "reason": "empty_url",
            "summary": "No URL found for this entry.",
        }

    prompt = (
        "You are the web/research half of a local assistant called the Machine Spirit.\n"
        "You have been given this URL to inspect:\n\n"
        f"{url}\n\n"
        "Visit and read the page if you can, then produce a clear summary of the main ideas "
        "that would be useful for future reference. Use plain text (no markdown). "
        "If you cannot access it, explain that clearly."
    )

    try:
        summary = web_respond(prompt)
    except Exception as e:
        summary = f"Research error while calling web_respond for URL {url}: {e!r}"

    return {
        "ok": True,
        "summary": summary,
        "source_url": url,
    }


# ---- main worker logic -----------------------------------------------------

def run_worker() -> None:
    mgr = ResearchManager()
    queue = mgr.get_queue()

    pending_indices = [i for i, e in enumerate(queue) if e.get("status") == "pending"]

    if not pending_indices:
        print("No pending research tasks in", RESEARCH_QUEUE_PATH)
        return

    print(f"Found {len(pending_indices)} pending research task(s). Processing...")

    local_knowledge = _load_json_dict(LOCAL_KNOWLEDGE_PATH)
    research_notes = _load_json_dict(RESEARCH_NOTES_PATH)

    for idx in pending_indices:
        entry = queue[idx]
        entry_type = entry.get("type")

        print(f"\n--- Task {idx} ---")
        print("Type:", entry_type)
        print("Reason:", entry.get("reason"))
        print("Channel:", entry.get("channel"))
        print("Status:", entry.get("status"))

        result: Dict[str, Any]

        if entry_type == "topic":
            print("Processing topic:", entry.get("user_text"))
            result = _process_topic(entry)

            if result.get("ok"):
                summary = result["summary"]
                question = result["source_question"]
                # Save into research_notes
                notes_key = f"topic::{question}"
                research_notes[notes_key] = {
                    "question": question,
                    "summary": summary,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
                # Also store into local_knowledge so the brain can answer later
                norm_q = _normalize_question(question)
                local_knowledge[norm_q] = summary
                entry["notes_key"] = notes_key
                entry["status"] = "done"
                print("  -> Stored summary in research_notes and local_knowledge.")
            else:
                entry["status"] = "error"
                entry["error"] = result.get("reason")
                print("  -> Failed to process topic:", result.get("reason"))

        elif entry_type == "url":
            print("Processing URL:", entry.get("url"))
            result = _process_url(entry)

            if result.get("ok"):
                summary = result["summary"]
                url = result["source_url"]
                notes_key = f"url::{url}"
                research_notes[notes_key] = {
                    "url": url,
                    "summary": summary,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
                entry["notes_key"] = notes_key
                entry["status"] = "done"
                print("  -> Stored URL summary in research_notes.")
            else:
                entry["status"] = "error"
                entry["error"] = result.get("reason")
                print("  -> Failed to process URL:", result.get("reason"))

        else:
            print("Unknown entry type, skipping.")
            entry["status"] = "error"
            entry["error"] = "unknown_type"

    # Save updated queue and knowledge files
    mgr.save_queue(queue)
    _save_json_dict(LOCAL_KNOWLEDGE_PATH, local_knowledge)
    _save_json_dict(RESEARCH_NOTES_PATH, research_notes)

    print("\nResearch worker completed.")
    print("Updated queue:", RESEARCH_QUEUE_PATH)
    print("Updated local knowledge:", LOCAL_KNOWLEDGE_PATH)
    print("Updated research notes:", RESEARCH_NOTES_PATH)


if __name__ == "__main__":
    run_worker()
