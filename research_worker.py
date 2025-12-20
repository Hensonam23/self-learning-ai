#!/usr/bin/env python3
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

KNOWLEDGE_PATH = os.path.join(DATA_DIR, "local_knowledge.json")
RESEARCH_QUEUE_PATH = os.path.join(DATA_DIR, "research_queue.json")
RESEARCH_NOTES_DIR = os.path.join(DATA_DIR, "research_notes")

MIN_NOTE_CHARS = 300


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def safe_read_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        print(f"Warning: JSON file is broken: {path}")
        return default


def safe_write_json(path: str, obj: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def normalize_topic(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def clamp_conf(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def load_knowledge() -> Dict[str, Dict[str, Any]]:
    data = safe_read_json(KNOWLEDGE_PATH, {})
    if not isinstance(data, dict):
        data = {}
    # normalize keys
    normalized = {}
    for k, v in data.items():
        if isinstance(v, dict):
            normalized[normalize_topic(k)] = v
    return normalized


def save_knowledge(knowledge: Dict[str, Dict[str, Any]]) -> None:
    safe_write_json(KNOWLEDGE_PATH, knowledge)


def load_queue() -> List[Dict[str, Any]]:
    q = safe_read_json(RESEARCH_QUEUE_PATH, [])
    if not isinstance(q, list):
        q = []
    return q


def save_queue(queue: List[Dict[str, Any]]) -> None:
    safe_write_json(RESEARCH_QUEUE_PATH, queue)


def topic_to_filename(topic: str) -> str:
    # safe filename
    t = normalize_topic(topic)
    t = re.sub(r"[^a-z0-9 _-]", "", t)
    t = t.replace(" ", "_")
    return t + ".txt"


def summarize_note(note: str, topic: str) -> str:
    # Simple offline summarizer
    # It picks key sentences and trims it into a clean explanation.
    cleaned = re.sub(r"\s+", " ", note).strip()
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)

    # prioritize sentences that contain the topic words
    words = [w for w in re.split(r"\W+", normalize_topic(topic)) if w]
    scored = []
    for s in sentences:
        ls = s.lower()
        score = 0
        for w in words:
            if w in ls:
                score += 2
        # prefer medium length
        if 60 <= len(s) <= 220:
            score += 1
        scored.append((score, s))

    scored.sort(reverse=True)
    picked = [s for score, s in scored[:6] if score > 0] or sentences[:4]
    picked = [p.strip() for p in picked if p.strip()]

    # ensure not crazy long
    summary = " ".join(picked)
    if len(summary) > 1200:
        summary = summary[:1200].rsplit(" ", 1)[0] + "..."
    return summary


def update_knowledge_from_research(knowledge: Dict[str, Dict[str, Any]], topic: str, summary: str) -> None:
    nt = normalize_topic(topic)
    existing = knowledge.get(nt)

    if existing:
        old_conf = float(existing.get("confidence", 0.0))
        # Research boosts confidence, but not instantly perfect
        new_conf = clamp_conf(max(old_conf, 0.65) + 0.10)
        existing["answer"] = summary.strip()
        existing["confidence"] = new_conf
        existing["source"] = "research_note"
        existing["last_updated"] = now_utc_iso()
        existing["notes"] = "Updated by research_worker from local research note"
        knowledge[nt] = existing
    else:
        knowledge[nt] = {
            "answer": summary.strip(),
            "confidence": 0.70,
            "source": "research_note",
            "last_updated": now_utc_iso(),
            "notes": "Created by research_worker from local research note",
        }


def main() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(RESEARCH_NOTES_DIR, exist_ok=True)

    knowledge = load_knowledge()
    queue = load_queue()

    pending = [x for x in queue if x.get("status") in ("pending", "in_progress")]
    if not pending:
        print("No pending research tasks. Queue is clear.")
        return

    updated_any = False

    for item in queue:
        if item.get("status") not in ("pending", "in_progress"):
            continue

        topic = str(item.get("topic", "")).strip()
        if not topic:
            item["status"] = "skipped"
            continue

        note_file = os.path.join(RESEARCH_NOTES_DIR, topic_to_filename(topic))

        if not os.path.exists(note_file):
            item["status"] = "pending"
            item["worker_note"] = f"Missing research note file: {note_file}"
            continue

        with open(note_file, "r", encoding="utf-8") as f:
            note = f.read()

        if len(note.strip()) < MIN_NOTE_CHARS:
            item["status"] = "pending"
            item["worker_note"] = f"Research note too short ({len(note.strip())} chars). Add more detail."
            continue

        item["status"] = "in_progress"
        summary = summarize_note(note, topic)
        update_knowledge_from_research(knowledge, topic, summary)

        item["status"] = "done"
        item["completed_on"] = now_utc_iso()
        item["worker_note"] = "Upgraded knowledge using local research note"
        updated_any = True
        updated_any = True

    save_queue(queue)
    if updated_any:
        save_knowledge(knowledge)

    # Print results
    still_pending = [x for x in queue if x.get("status") in ("pending", "in_progress")]
    done = [x for x in queue if x.get("status") == "done"]

    print(f"Completed: {len(done)}")
    print(f"Still pending: {len(still_pending)}")

    if still_pending:
        print("\nTo complete pending topics, add a note file for each topic in:")
        print(f"  {RESEARCH_NOTES_DIR}")
        print("File name example:")
        print("  osi_model.txt")
        print("\nThen run:")
        print("  python3 research_worker.py")


if __name__ == "__main__":
    main()
