import json
import os

# This script only edits data files, it does not run the interactive brain.
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")
QUEUE_PATH = os.path.join(DATA, "research_queue.json")
NOTES_DIR = os.path.join(DATA, "research_notes")
KNOWLEDGE_PATH = os.path.join(DATA, "local_knowledge.json")

def load(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

def norm(s):
    return " ".join(s.strip().lower().split())

def topic_to_file(topic):
    t = norm(topic).replace(" ", "_")
    out = "".join(ch for ch in t if ch.isalnum() or ch == "_")
    while "__" in out:
        out = out.replace("__", "_")
    out = out.strip("_")
    if not out:
        out = "untitled"
    return out + ".txt"

def main():
    os.makedirs(NOTES_DIR, exist_ok=True)
    queue = load(QUEUE_PATH, [])
    knowledge = load(KNOWLEDGE_PATH, {})

    upgraded = 0
    checked = 0

    for item in queue:
        if item.get("status") != "pending":
            continue
        topic = norm(item.get("topic", ""))
        if not topic:
            continue
        checked += 1

        note_path = os.path.join(NOTES_DIR, topic_to_file(topic))
        if not os.path.exists(note_path):
            continue

        try:
            with open(note_path, "r", encoding="utf-8") as f:
                txt = f.read().strip()
        except Exception:
            continue

        if not txt:
            continue

        entry = knowledge.get(topic, {})
        conf = float(entry.get("confidence", 0.0))
        conf = max(conf, 0.65)

        knowledge[topic] = {
            "answer": txt,
            "confidence": conf,
            "updated_on": __import__("datetime").date.today().isoformat(),
            "notes": f"Auto upgraded by timer from {os.path.basename(note_path)}",
        }

        item["status"] = "done"
        item["completed_on"] = __import__("datetime").date.today().isoformat()
        upgraded += 1

    save(KNOWLEDGE_PATH, knowledge)
    save(QUEUE_PATH, queue)

    # Print for journald logs
    print(f"autoupgrade: upgraded={upgraded} checked={checked}")

if __name__ == "__main__":
    main()
