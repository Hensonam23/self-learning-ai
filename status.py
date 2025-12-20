import json
import os
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        return default

def file_mtime(path):
    try:
        ts = os.path.getmtime(path)
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except FileNotFoundError:
        return "missing"

def count_items(obj):
    if isinstance(obj, dict):
        return len(obj)
    if isinstance(obj, list):
        return len(obj)
    return 0

def main():
    local_knowledge_path = os.path.join(DATA_DIR, "local_knowledge.json")
    taught_path = os.path.join(DATA_DIR, "taught_knowledge.json")
    queue_path = os.path.join(DATA_DIR, "research_queue.json")
    notes_path = os.path.join(DATA_DIR, "research_notes.json")
    aliases_path = os.path.join(DATA_DIR, "aliases.json")
    template_requests_path = os.path.join(DATA_DIR, "template_requests.json")

    local = load_json(local_knowledge_path, {})
    taught = load_json(taught_path, {})
    queue = load_json(queue_path, [])
    notes = load_json(notes_path, {})
    aliases = load_json(aliases_path, {})
    templates = load_json(template_requests_path, [])

    pending = 0
    done = 0
    if isinstance(queue, list):
        for item in queue:
            if isinstance(item, dict):
                s = str(item.get("status", "")).lower()
                if s == "pending":
                    pending += 1
                elif s == "done":
                    done += 1

    notes_count = 0
    if isinstance(notes, dict) and isinstance(notes.get("drafts"), dict):
        notes_count = len(notes["drafts"])
    elif isinstance(notes, dict):
        notes_count = len(notes)

    print("\n=== MACHINE SPIRIT STATUS ===\n")

    print("Knowledge files:")
    print(f"  local_knowledge.json     entries: {count_items(local)}    updated: {file_mtime(local_knowledge_path)}")
    print(f"  taught_knowledge.json    entries: {count_items(taught)}   updated: {file_mtime(taught_path)}")
    print(f"  research_notes.json      entries: {notes_count}           updated: {file_mtime(notes_path)}")
    print(f"  aliases.json             entries: {count_items(aliases)}  updated: {file_mtime(aliases_path)}")

    print("\nQueues:")
    print(f"  research_queue.json      total: {count_items(queue)} (pending: {pending}, done: {done})  updated: {file_mtime(queue_path)}")
    print(f"  template_requests.json   topics: {count_items(templates)}  updated: {file_mtime(template_requests_path)}")

    print("\nLogs:")
    print(f"  watcher.log: {file_mtime(os.path.join(os.path.dirname(__file__), 'logs', 'watcher.log'))}")
    print(f"  startup.log: {file_mtime(os.path.join(os.path.dirname(__file__), 'logs', 'startup.log'))}")

    print("\nRun:")
    print("  python3 status.py\n")

if __name__ == "__main__":
    main()
