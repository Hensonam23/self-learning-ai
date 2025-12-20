#!/usr/bin/env python3

import json
import os
import time
import shutil
from datetime import datetime

# =========================
# Paths
# =========================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")

KNOWLEDGE_PATH = os.path.join(DATA_DIR, "local_knowledge.json")
ALIASES_PATH = os.path.join(DATA_DIR, "topic_aliases.json")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

# =========================
# Utility
# =========================

def normalize_topic(topic: str) -> str:
    if not topic:
        return ""
    t = topic.strip().lower()
    while "  " in t:
        t = t.replace("  ", " ")
    t = t.rstrip(" .!?")
    return t

# =========================
# Knowledge
# =========================

def load_knowledge() -> dict:
    if not os.path.exists(KNOWLEDGE_PATH):
        return {}
    try:
        with open(KNOWLEDGE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}

def save_knowledge(knowledge: dict) -> None:
    with open(KNOWLEDGE_PATH, "w", encoding="utf-8") as f:
        json.dump(knowledge, f, indent=2, ensure_ascii=False)

# =========================
# Aliases
# =========================

def load_aliases() -> dict:
    if not os.path.exists(ALIASES_PATH):
        return {}
    try:
        with open(ALIASES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            cleaned = {}
            for k, v in data.items():
                nk = normalize_topic(str(k))
                nv = normalize_topic(str(v))
                if nk and nv:
                    cleaned[nk] = nv
            return cleaned
    except Exception:
        pass
    return {}

def save_aliases(aliases: dict) -> None:
    with open(ALIASES_PATH, "w", encoding="utf-8") as f:
        json.dump(aliases, f, indent=2, ensure_ascii=False)

def resolve_topic(topic: str, aliases: dict) -> str:
    t = normalize_topic(topic)
    seen = set()
    while t in aliases and t not in seen:
        seen.add(t)
        t = normalize_topic(aliases.get(t, t))
    return t

# =========================
# Backup system (timer-based)
# =========================

LAST_BACKUP_TIME = 0
BACKUP_INTERVAL = 300  # seconds (5 minutes)

def backup_knowledge_if_needed():
    global LAST_BACKUP_TIME
    now = time.time()
    if now - LAST_BACKUP_TIME < BACKUP_INTERVAL:
        return

    if not os.path.exists(KNOWLEDGE_PATH):
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"local_knowledge_{timestamp}.json"
    backup_path = os.path.join(BACKUP_DIR, backup_name)

    try:
        shutil.copy2(KNOWLEDGE_PATH, backup_path)
        LAST_BACKUP_TIME = now
    except Exception:
        pass

# =========================
# Main Brain Loop
# =========================

def main():
    print("Machine Spirit brain online. Type a message, Ctrl+C to exit.")

    knowledge = load_knowledge()
    aliases = load_aliases()

    try:
        while True:
            backup_knowledge_if_needed()

            user_input = input("> ").strip()
            if not user_input:
                continue

            # =====================
            # Commands
            # =====================

            if user_input.startswith("/"):
                parts = user_input.split(" ", 1)
                cmd = parts[0]
                rest = parts[1] if len(parts) > 1 else ""

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
                        "confidence": 0.7,
                        "last_updated": datetime.now().isoformat(),
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

                if cmd == "/exit":
                    print("Shutting down.")
                    break

                print("Unknown command.")
                continue

            # =====================
            # Normal questions
            # =====================

            topic = resolve_topic(user_input, aliases)

            if topic in knowledge:
                print(knowledge[topic]["answer"])
            else:
                print(
                    "I do not have a taught answer for that yet. "
                    "If my reply is wrong or weak, correct me in your own words "
                    "and I will remember it."
                )

    except KeyboardInterrupt:
        print("\nShutting down.")

# =========================

if __name__ == "__main__":
    main()
