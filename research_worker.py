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
    t = normalize_topic(topic)
    t = re.sub(r"[^a-z0-9 _-]", "", t)
    t = t.replace(" ", "_")
    return t + ".txt"


def summarize_note(note: str, topic: str) -> str:
    cleaned = re.sub(r"\s+", " ", note).strip()
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)

    words = [w for w in re.split(r"\W+", normalize_topic(topic)) if w]
    scored = []
    for s in sentences:
        ls = s.lower()
        score = 0
        for w in words:
            if w in ls:
                score += 2
        if 60 <= len(s) <= 220:
            score += 1
        scored.append((score, s))

    scored.sort(reverse=True)
    picked = [s for score, s in scored[:7] if score > 0] or sentences[:5]
    picked = [p.strip() for p in picked if p.strip()]

    summary = " ".join(picked)
    if len(summary) > 1200:
        summary = summary[:1200].rsplit(" ", 1)[0] + "..."
    return summary


def update_knowledge_from_research(knowledge: Dict[str, Dict[str, Any]], topic: str, summary: str) -> None:
    nt = normalize_topic(topic)
    existing = knowledge.get(nt)

    if existing:
        old_conf = float(existing.get("confidence", 0.0))
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


# ---------- READY-TO-PASTE NOTE TEMPLATES (OFFLINE) ----------

def note_template(topic: str) -> Optional[str]:
    t = normalize_topic(topic)

    templates: Dict[str, str] = {
        "osi model": """OSI Model (Open Systems Interconnection) – Research Note

The OSI model is a 7-layer framework used to describe how network communication works. It is not a specific protocol you “run,” but a way to organize and explain how data moves from an application on one device to an application on another device. Each layer has a specific job and passes data up or down to the next layer.

Layer 7 – Application: This is what the user interacts with (web browsing, email, file transfer). Examples often associated here include HTTP, HTTPS, SMTP, DNS, and FTP, but the key idea is “services used by applications.”

Layer 6 – Presentation: This layer focuses on how data is formatted so both sides can understand it. It deals with things like encoding, compression, and encryption. Example concepts include TLS/SSL encryption and data formats.

Layer 5 – Session: This layer manages the “conversation” between two devices. It helps start, maintain, and end sessions. It also helps with checkpointing and recovery in long communications.

Layer 4 – Transport: This is where reliability and delivery rules live. TCP provides reliable delivery with sequencing and retransmissions. UDP is faster and simpler but does not guarantee delivery. This layer uses ports (like 80, 443, 53) so the right app receives the data.

Layer 3 – Network: This is about routing between networks. IP addressing and routers operate here. The main goal is to move packets from one network to another using logical addresses (IP).

Layer 2 – Data Link: This is local network delivery on the same network segment. It uses MAC addresses and frames. Switches typically operate here. It also includes error detection like CRC.

Layer 1 – Physical: This is the raw transmission layer. It includes cables, radio signals (Wi-Fi), connectors, voltages, and physical bit transmission.

A simple way to remember it is: the top layers deal with user data and formatting, the middle layers deal with moving and controlling the conversation, and the bottom layers deal with local delivery and physical signals.

The OSI model helps with troubleshooting by letting you isolate problems: cable issues at layer 1, switching/MAC issues at layer 2, routing/IP issues at layer 3, TCP/UDP and port issues at layer 4, and so on.
""",
    }

    return templates.get(t)


# -----------------------------------------------------------

def main() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(RESEARCH_NOTES_DIR, exist_ok=True)

    knowledge = load_knowledge()
    queue = load_queue()

    pending_items = [x for x in queue if x.get("status") in ("pending", "in_progress")]
    if not pending_items:
        print("No pending research tasks. Queue is clear.")
        return

    updated_any = False
    missing_notes: List[Dict[str, str]] = []

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
            missing_notes.append({"topic": topic, "file": note_file})
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

    save_queue(queue)
    if updated_any:
        save_knowledge(knowledge)

    done = [x for x in queue if x.get("status") == "done"]
    still_pending = [x for x in queue if x.get("status") in ("pending", "in_progress")]

    print(f"Completed: {len(done)}")
    print(f"Still pending: {len(still_pending)}")

    # If missing notes exist, show instructions + template if available
    if missing_notes:
        print("\nMissing note files detected. Create these files and paste the note content:\n")
        for m in missing_notes[:10]:
            topic = m["topic"]
            file_path = m["file"]
            print(f"- Topic: {topic}")
            print(f"  File:  {file_path}")

            tmpl = note_template(topic)
            if tmpl:
                print("\n  READY-TO-PASTE NOTE BELOW:\n")
                print("  ---------- COPY FROM HERE ----------")
                print(tmpl.strip())
                print("  ----------- COPY TO HERE -----------\n")
            else:
                print("  No template available yet. Ask me and I will generate a paste-ready note.\n")

        print("After creating the notes, run:")
        print("  python3 research_worker.py")


if __name__ == "__main__":
    main()
