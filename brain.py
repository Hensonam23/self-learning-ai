#!/usr/bin/env python3

import json
import os
import time
import shutil
from datetime import datetime, date

# =========================
# Paths
# =========================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")

KNOWLEDGE_PATH = os.path.join(DATA_DIR, "local_knowledge.json")
ALIASES_PATH = os.path.join(DATA_DIR, "topic_aliases.json")
RESEARCH_QUEUE_PATH = os.path.join(DATA_DIR, "research_queue.json")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

# =========================
# Tuning (Confidence)
# =========================

CONF_DECAY_PER_DAY = 0.02
CONF_REINFORCE_ON_USE = 0.01

CONF_MIN = 0.10
CONF_MAX = 0.95
CONF_LOW_THRESHOLD = 0.50

# For manual promote command
CONF_PROMOTE_DEFAULT = 0.10

# =========================
# Backup system (timer-based)
# =========================

LAST_BACKUP_TIME = 0
BACKUP_INTERVAL = 300  # seconds (5 minutes)

def backup_files_if_needed():
    global LAST_BACKUP_TIME

    now = time.time()
    if now - LAST_BACKUP_TIME < BACKUP_INTERVAL:
        return

    files_to_backup = [
        KNOWLEDGE_PATH,
        ALIASES_PATH,
        RESEARCH_QUEUE_PATH
    ]

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    backed_any = False
    for fp in files_to_backup:
        if not os.path.exists(fp):
            continue

        base = os.path.basename(fp).replace(".json", "")
        backup_name = f"{base}_{timestamp}.json"
        backup_path = os.path.join(BACKUP_DIR, backup_name)

        try:
            shutil.copy2(fp, backup_path)
            backed_any = True
        except Exception:
            pass

    if backed_any:
        LAST_BACKUP_TIME = now

# =========================
# Utility
# =========================

def now_iso() -> str:
    return datetime.now().isoformat()

def normalize_topic(topic: str) -> str:
    if not topic:
        return ""
    t = topic.strip().lower()
    while "  " in t:
        t = t.replace("  ", " ")
    t = t.rstrip(" .!?")
    return t

def parse_iso_dt(s: str):
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None

# =========================
# JSON Load/Save
# =========================

def load_json_dict(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def save_json_dict(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def load_json_list(path: str) -> list:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []

def save_json_list(path: str, data: list) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# =========================
# Knowledge
# =========================

def load_knowledge() -> dict:
    return load_json_dict(KNOWLEDGE_PATH)

def save_knowledge(knowledge: dict) -> None:
    save_json_dict(KNOWLEDGE_PATH, knowledge)

def ensure_knowledge_shape(entry: dict) -> dict:
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

def apply_confidence_decay(entry: dict) -> dict:
    entry = ensure_knowledge_shape(entry)

    lu = parse_iso_dt(entry.get("last_used", "")) or parse_iso_dt(entry.get("last_updated", ""))
    if not lu:
        return entry

    days_old = (datetime.now() - lu).total_seconds() / 86400.0
    if days_old <= 0:
        return entry

    decayed = entry["confidence"] - (days_old * CONF_DECAY_PER_DAY)
    if decayed < CONF_MIN:
        decayed = CONF_MIN

    entry["confidence"] = round(float(decayed), 4)
    return entry

def reinforce_on_use(entry: dict) -> dict:
    entry = ensure_knowledge_shape(entry)
    boosted = entry["confidence"] + CONF_REINFORCE_ON_USE
    if boosted > CONF_MAX:
        boosted = CONF_MAX
    entry["confidence"] = round(float(boosted), 4)
    entry["last_used"] = now_iso()
    return entry

def promote_confidence(entry: dict, amount: float) -> dict:
    entry = ensure_knowledge_shape(entry)
    boosted = float(entry.get("confidence", 0.40)) + float(amount)
    if boosted > CONF_MAX:
        boosted = CONF_MAX
    if boosted < CONF_MIN:
        boosted = CONF_MIN
    entry["confidence"] = round(float(boosted), 4)
    entry["last_used"] = now_iso()
    return entry

# =========================
# Aliases
# =========================

def load_aliases() -> dict:
    raw = load_json_dict(ALIASES_PATH)
    cleaned = {}
    for k, v in raw.items():
        nk = normalize_topic(str(k))
        nv = normalize_topic(str(v))
        if nk and nv:
            cleaned[nk] = nv
    return cleaned

def save_aliases(aliases: dict) -> None:
    save_json_dict(ALIASES_PATH, aliases)

def resolve_topic(topic: str, aliases: dict) -> str:
    t = normalize_topic(topic)
    seen = set()
    while t in aliases and t not in seen:
        seen.add(t)
        t = normalize_topic(aliases.get(t, t))
    return t

# =========================
# Research Queue
# =========================

def load_research_queue() -> list:
    return load_json_list(RESEARCH_QUEUE_PATH)

def save_research_queue(queue: list) -> None:
    save_json_list(RESEARCH_QUEUE_PATH, queue)

def enqueue_research(queue: list, topic: str, reason: str, current_confidence: float) -> list:
    t = normalize_topic(topic)
    for item in queue:
        if normalize_topic(str(item.get("topic", ""))) == t and item.get("status") in ("pending", "in_progress"):
            return queue

    queue.append({
        "topic": t,
        "reason": reason,
        "requested_on": str(date.today()),
        "status": "pending",
        "current_confidence": round(float(current_confidence), 4),
        "worker_note": ""
    })
    return queue

def pending_queue(queue: list) -> list:
    return [q for q in queue if q.get("status") in ("pending", "in_progress")]

# =========================
# Commands
# =========================

HELP_TEXT = """
Commands:
  /teach <topic> | <answer>
  /alias <alias> | <topic>
  /show <topic>
  /low [n]
  /queue [n]
  /promote <topic> [amount]
  /help
  /exit
""".strip()

# =========================
# Main Brain Loop
# =========================

def main():
    print("Machine Spirit brain online. Type a message, Ctrl+C to exit.")
    print("Type /help for commands.")

    knowledge = load_knowledge()
    aliases = load_aliases()
    research_queue = load_research_queue()

    try:
        while True:
            backup_files_if_needed()

            user_input = input("> ").strip()
            if not user_input:
                continue

            # =====================
            # Commands
            # =====================

            if user_input.startswith("/"):
                parts = user_input.split(" ", 1)
                cmd = parts[0].strip()
                rest = parts[1] if len(parts) > 1 else ""

                if cmd == "/help":
                    print(HELP_TEXT)
                    continue

                if cmd == "/exit":
                    print("Shutting down.")
                    break

                if cmd == "/teach":
                    if "|" not in rest:
                        print("Usage: /teach <topic> | <answer>")
                        continue

                    left, right = rest.split("|", 1)
                    topic = resolve_topic(left, aliases)
                    answer = right.strip()

                    if not topic or not answer:
                        print("Usage: /teach <topic> | <answer>")
                        continue

                    knowledge[topic] = {
                        "answer": answer,
                        "confidence": 0.75,
                        "last_updated": now_iso(),
                        "last_used": now_iso(),
                        "notes": "Taught by user"
                    }

                    save_knowledge(knowledge)
                    print(f"Taught: {topic}")
                    continue

                if cmd == "/alias":
                    if "|" not in rest:
                        print("Usage: /alias <alias> | <topic>")
                        continue

                    left, right = rest.split("|", 1)
                    alias_key = normalize_topic(left)
                    canonical = resolve_topic(right, aliases)

                    if not alias_key or not canonical:
                        print("Usage: /alias <alias> | <topic>")
                        continue

                    if alias_key == canonical:
                        print("Alias and topic resolve to the same value.")
                        continue

                    aliases[alias_key] = canonical
                    save_aliases(aliases)
                    print(f"Alias saved: '{alias_key}' -> '{canonical}'")
                    continue

                if cmd == "/show":
                    if not rest.strip():
                        print("Usage: /show <topic>")
                        continue

                    topic = resolve_topic(rest, aliases)
                    if topic not in knowledge:
                        print("No taught answer for that topic.")
                        continue

                    entry = apply_confidence_decay(knowledge[topic])
                    knowledge[topic] = entry
                    save_knowledge(knowledge)

                    print(f"Topic: {topic}")
                    print(f"Confidence: {entry.get('confidence')}")
                    print(f"Last updated: {entry.get('last_updated')}")
                    print(f"Last used: {entry.get('last_used')}")
                    print(f"Notes: {entry.get('notes')}")
                    continue

                if cmd == "/low":
                    n = 10
                    if rest.strip():
                        try:
                            n = int(rest.strip())
                        except Exception:
                            n = 10

                    for k in list(knowledge.keys()):
                        knowledge[k] = apply_confidence_decay(knowledge[k])

                    items = []
                    for k, v in knowledge.items():
                        v = ensure_knowledge_shape(v)
                        items.append((k, float(v.get("confidence", 0.0))))

                    items.sort(key=lambda x: x[1])
                    save_knowledge(knowledge)

                    print(f"Lowest confidence topics (top {n}):")
                    for t, c in items[:n]:
                        print(f"  {t}  (conf={c})")
                    continue

                if cmd == "/queue":
                    n = 10
                    if rest.strip():
                        try:
                            n = int(rest.strip())
                        except Exception:
                            n = 10

                    research_queue = load_research_queue()
                    pend = pending_queue(research_queue)

                    if not pend:
                        print("No pending research tasks. Queue is clear.")
                        continue

                    print(f"Pending research tasks (top {n}):")
                    for item in pend[:n]:
                        t = item.get("topic", "")
                        r = item.get("reason", "")
                        c = item.get("current_confidence", "")
                        print(f"  {t}  (conf={c})  reason={r}")
                    continue

                if cmd == "/promote":
                    if not rest.strip():
                        print("Usage: /promote <topic> [amount]")
                        continue

                    bits = rest.strip().split()
                    amount = CONF_PROMOTE_DEFAULT

                    if len(bits) >= 2:
                        try:
                            amount = float(bits[-1])
                            topic_text = " ".join(bits[:-1])
                        except Exception:
                            topic_text = rest.strip()
                    else:
                        topic_text = rest.strip()

                    topic = resolve_topic(topic_text, aliases)

                    if topic not in knowledge:
                        print("No taught answer for that topic yet. Teach it first.")
                        continue

                    knowledge[topic] = apply_confidence_decay(knowledge[topic])
                    knowledge[topic] = promote_confidence(knowledge[topic], amount)

                    old_notes = str(knowledge[topic].get("notes", "")).strip()
                    stamp = f"Promoted by user (+{amount})"
                    knowledge[topic]["notes"] = (old_notes + " | " + stamp).strip(" |")

                    save_knowledge(knowledge)
                    print(f"Promoted: {topic}  new_conf={knowledge[topic].get('confidence')}")
                    continue

                print("Unknown command. Type /help.")
                continue

            # =====================
            # Normal questions
            # =====================

            topic = resolve_topic(user_input, aliases)

            if topic in knowledge:
                knowledge[topic] = apply_confidence_decay(knowledge[topic])
                knowledge[topic] = reinforce_on_use(knowledge[topic])
                save_knowledge(knowledge)
                print(knowledge[topic]["answer"])

                conf = float(knowledge[topic].get("confidence", 0.0))
                if conf < CONF_LOW_THRESHOLD:
                    research_queue = enqueue_research(
                        research_queue,
                        topic,
                        reason="Answer exists but confidence is low",
                        current_confidence=conf
                    )
                    save_research_queue(research_queue)

            else:
                research_queue = enqueue_research(
                    research_queue,
                    topic,
                    reason="No taught answer yet",
                    current_confidence=0.30
                )
                save_research_queue(research_queue)

                print(
                    "I do not have a taught answer for that yet. "
                    "If my reply is wrong or weak, correct me in your own words "
                    "and I will remember it. "
                    "I also marked this topic for deeper research so I can improve over time."
                )

    except KeyboardInterrupt:
        print("\nShutting down.")

if __name__ == "__main__":
    main()
