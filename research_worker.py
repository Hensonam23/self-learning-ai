#!/usr/bin/env python3

import json
import os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

KNOWLEDGE_PATH = os.path.join(DATA_DIR, "local_knowledge.json")
RESEARCH_QUEUE_PATH = os.path.join(DATA_DIR, "research_queue.json")
RESEARCH_NOTES_DIR = os.path.join(DATA_DIR, "research_notes")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(RESEARCH_NOTES_DIR, exist_ok=True)

# Worker tuning
CONF_BOOST_ON_NOTE = 0.20
CONF_MAX = 0.95


def now_iso() -> str:
    return datetime.now().isoformat()


def safe_slug(topic: str) -> str:
    t = (topic or "").strip().lower()
    out = []
    for ch in t:
        if ch.isalnum():
            out.append(ch)
        else:
            out.append("_")
    slug = "".join(out)
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_") or "topic"


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def ensure_entry_shape(entry: dict) -> dict:
    if "answer" not in entry:
        entry["answer"] = ""
    if "confidence" not in entry or not isinstance(entry["confidence"], (int, float)):
        entry["confidence"] = 0.40
    if "last_updated" not in entry:
        entry["last_updated"] = now_iso()
    if "last_used" not in entry:
        entry["last_used"] = entry["last_updated"]
    if "notes" not in entry:
        entry["notes"] = ""
    return entry


def build_notes_template(topic: str) -> str:
    return (
        f"Topic: {topic}\n"
        f"Created: {now_iso()}\n\n"
        "Goal:\n"
        "- Write a better answer for this topic in your own words.\n\n"
        "Good sources / key facts:\n"
        "- \n\n"
        "My improved answer (paste below):\n"
        "- \n"
    )


def main():
    knowledge = load_json(KNOWLEDGE_PATH, {})
    if not isinstance(knowledge, dict):
        knowledge = {}

    queue = load_json(RESEARCH_QUEUE_PATH, [])
    if not isinstance(queue, list):
        queue = []

    pending = [q for q in queue if q.get("status") == "pending"]

    if not pending:
        print("No pending research tasks. Queue is clear.")
        return

    did_any = False

    for item in queue:
        if item.get("status") != "pending":
            continue

        topic = (item.get("topic") or "").strip()
        if not topic:
            item["status"] = "done"
            item["worker_note"] = "Skipped: empty topic"
            continue

        slug = safe_slug(topic)
        note_path = os.path.join(RESEARCH_NOTES_DIR, f"{slug}.txt")

        # 1) If notes file doesn't exist, create it and leave task pending
        if not os.path.exists(note_path):
            with open(note_path, "w", encoding="utf-8") as f:
                f.write(build_notes_template(topic))

            item["worker_note"] = f"Created research note file: {note_path}"
            # Keep pending so you can fill it in and run worker again
            did_any = True
            continue

        # 2) Notes file exists -> try to ingest "My improved answer" section
        try:
            with open(note_path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception:
            item["worker_note"] = f"Could not read note file: {note_path}"
            did_any = True
            continue

        marker = "My improved answer (paste below):"
        if marker not in text:
            item["worker_note"] = f"Note file missing marker section: {note_path}"
            did_any = True
            continue

        improved = text.split(marker, 1)[1].strip()

        # If user hasn't filled it in yet, keep pending
        if not improved or improved == "-" or improved.startswith("- \n") or improved.startswith("-"):
            item["worker_note"] = f"Note file exists but improved answer is still empty: {note_path}"
            did_any = True
            continue

        # Upgrade knowledge entry
        entry = knowledge.get(topic, {})
        if not isinstance(entry, dict):
            entry = {}

        entry = ensure_entry_shape(entry)
        entry["answer"] = improved
        entry["last_updated"] = now_iso()
        entry["notes"] = (entry.get("notes", "") + " | Upgraded by research_worker").strip(" |")

        # Boost confidence
        new_conf = float(entry.get("confidence", 0.40)) + CONF_BOOST_ON_NOTE
        if new_conf > CONF_MAX:
            new_conf = CONF_MAX
        entry["confidence"] = round(new_conf, 4)

        knowledge[topic] = entry

        item["status"] = "done"
        item["completed_on"] = str(datetime.now().date())
        item["worker_note"] = f"Upgraded knowledge using note file: {note_path}"

        did_any = True

    # Save updates
    save_json(KNOWLEDGE_PATH, knowledge)
    save_json(RESEARCH_QUEUE_PATH, queue)

    if did_any:
        # Print the first pending item status so you can see what it did fast
        still_pending = [q for q in queue if q.get("status") == "pending"]
        done = [q for q in queue if q.get("status") == "done"]

        print(f"Done items: {len(done)}")
        print(f"Still pending: {len(still_pending)}")

        if still_pending:
            print("\nNext pending task:")
            print(json.dumps(still_pending[0], indent=2))
        else:
            print("\nAll tasks complete.")
    else:
        print("No changes made.")


if __name__ == "__main__":
    main()
