from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from datetime import datetime, timezone

APP_ROOT = Path(__file__).resolve().parent
QUEUE_PATH = APP_ROOT / "data" / "research_queue.json"
NOTES_PATH = APP_ROOT / "data" / "research_notes.json"
BASE_PATH = APP_ROOT / "data" / "knowledge" / "base_knowledge.json"


# --------------------
# Helpers
# --------------------
def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def normalize(text):
    t = text.lower().strip()
    t = re.sub(r"[?!.]+$", "", t)
    return t


# --------------------
# Weak draft detection
# --------------------
def is_weak(text: str) -> bool:
    t = (text or "").lower()
    if len(t) < 40:
        return True
    weak_markers = [
        "write a clean",
        "main point",
        "basic draft",
        "might be rough",
        "(one sentence)",
        "( )",
    ]
    return any(m in t for m in weak_markers)


def draft_is_weak(draft: dict) -> bool:
    if not isinstance(draft, dict):
        return True
    short = draft.get("short", "")
    detailed = draft.get("detailed", "")
    return is_weak(short) or is_weak(detailed)


# --------------------
# Concept classification
# --------------------
def classify(topic: str) -> str:
    t = topic.lower()
    if " vs " in t or "difference between" in t:
        return "comparison"
    if any(k in t for k in ["protocol", "icmp", "tcp", "udp", "arp", "http"]):
        return "protocol"
    if any(k in t for k in ["process", "authentication", "boot", "transmission"]):
        return "process"
    if any(k in t for k in ["os", "system", "application", "software", "database"]):
        return "software"
    return "object"


# --------------------
# Learn style from taught knowledge
# --------------------
def load_style():
    base = load(BASE_PATH, {"items": {}})
    items = base.get("items", {})
    lengths = []

    for v in items.values():
        ans = v.get("answer", "")
        for s in re.split(r"[.!?]", ans):
            words = s.strip().split()
            if 5 <= len(words) <= 25:
                lengths.append(len(words))

    avg = int(sum(lengths) / len(lengths)) if lengths else 14
    return max(10, min(avg, 20))


# --------------------
# Synthesis engine
# --------------------
def synthesize(topic: str, kind: str, max_words: int):
    name = topic.strip()

    short = f"{name} is a {kind} related to computing or networking."

    if kind == "object":
        detailed = (
            f"{name} is an object used in computing systems.\n\n"
            f"It is designed to perform a specific role and is made up of components that work together.\n"
            f"In real systems, {name.lower()} helps process data, communicate, or store information."
        )
    elif kind == "software":
        detailed = (
            f"{name} is software used in computer systems.\n\n"
            f"It controls hardware or allows users to perform tasks.\n"
            f"Most systems rely on {name.lower()} to function correctly."
        )
    elif kind == "process":
        detailed = (
            f"{name} is a process that occurs in computer systems.\n\n"
            f"It involves a series of steps that allow systems or users to complete an action.\n"
            f"This process is important for normal operation or security."
        )
    elif kind == "protocol":
        detailed = (
            f"{name} is a networking protocol.\n\n"
            f"It defines rules for communication between devices.\n"
            f"Protocols like {name.lower()} ensure data is sent and understood correctly."
        )
    else:
        detailed = (
            f"{name} compares two related technologies.\n\n"
            f"It explains how each one works and when one is preferred over the other."
        )

    def trim(text):
        out = []
        for line in text.splitlines():
            words = line.split()
            if len(words) > max_words:
                out.append(" ".join(words[:max_words]) + ".")
            else:
                out.append(line)
        return "\n".join(out)

    return trim(short), trim(detailed)


# --------------------
# Main worker
# --------------------
def main():
    queue = load(QUEUE_PATH, {"queue": []})
    notes = load(NOTES_PATH, {"drafts": {}})
    style_words = load_style()

    tasks = queue.get("queue", [])
    if not tasks:
        print("No pending research tasks.")
        return

    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    wrote = 0

    while tasks and wrote < limit:
        item = tasks.pop(0)
        topic = item.get("topic", "")
        key = normalize(topic)

        existing = notes["drafts"].get(key)
        if existing and not draft_is_weak(existing):
            # Keep good drafts
            continue

        kind = classify(topic)
        short, detailed = synthesize(topic, kind, style_words)

        notes["drafts"][key] = {
            "topic": topic,
            "key": key,
            "type": kind,
            "short": short,
            "detailed": detailed,
            "confidence": "low",
            "created_at": now(),
            "source": "synthesized_auto_regen_v2",
        }

        print(f"Generated draft: {topic}")
        wrote += 1

    queue["queue"] = tasks
    save(QUEUE_PATH, queue)
    save(NOTES_PATH, notes)

    print(f"Done. Generated {wrote} draft(s).")


if __name__ == "__main__":
    main()
