#!/usr/bin/env python3
import sys, json, datetime
from pathlib import Path

def die(msg: str, code: int = 1):
    print(msg)
    sys.exit(code)

def load_json(path: Path, default):
    if not path.exists():
        return default
    txt = path.read_text(encoding="utf-8", errors="replace").strip()
    if not txt:
        return default
    return json.loads(txt)

def backup(path: Path):
    if not path.exists():
        return None
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    b = path.with_name(path.name + f".bak.{stamp}")
    b.write_text(path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    return b.name

def norm(s: str) -> str:
    return (s or "").strip().lower()

def main():
    if len(sys.argv) < 2:
        die('Usage: scripts/force_relearn_topic.py "topic" [--wipe] [--reason "text"]')

    topic = sys.argv[1]
    wipe = "--wipe" in sys.argv

    reason = "FORCE relearn"
    if "--reason" in sys.argv:
        i = sys.argv.index("--reason")
        if i + 1 >= len(sys.argv):
            die("ERROR: --reason needs a value")
        reason = "FORCE " + sys.argv[i + 1].strip()

    now = datetime.datetime.now().isoformat(timespec="seconds")

    rq = Path("data/research_queue.json")
    lk = Path("data/local_knowledge.json")

    brq = backup(rq)
    blk = backup(lk)

    q = load_json(rq, [])
    if not isinstance(q, list):
        die("ERROR: research_queue.json is not a list")

    # Find or create queue item
    found = False
    for item in q:
        if norm(item.get("topic")) == norm(topic):
            found = True
            item["status"] = "pending"
            item["reason"] = reason
            item["requested_on"] = now
            item["current_confidence"] = 0.0

            # Always clear these (they cause cooldown/skip behavior)
            for k in [
                "completed_on",
                "last_attempt_on",
                "last_attempt",
                "cooldown_until",
                "cooldown",
            ]:
                item.pop(k, None)

            # If wipe, also clear anything url-ish or attempt-ish so we truly relearn fresh
            if wipe:
                for k in list(item.keys()):
                    kl = k.lower()
                    if "url" in kl or "source" in kl or "chosen_url" in kl:
                        item.pop(k, None)
                    if "attempt" in kl or "cooldown" in kl:
                        item.pop(k, None)
                # reset attempts explicitly if your code uses it
                item["attempts"] = 0

            break

    if not found:
        item = {
            "topic": topic,
            "reason": reason,
            "requested_on": now,
            "status": "pending",
            "current_confidence": 0.0,
        }
        if wipe:
            item["attempts"] = 0
        q.append(item)

    rq.write_text(json.dumps(q, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Wipe local knowledge if requested
    if wipe:
        k = load_json(lk, {})
        if isinstance(k, dict):
            # delete exact or case-insensitive match
            if topic in k:
                k.pop(topic, None)
            for kk in list(k.keys()):
                if isinstance(kk, str) and norm(kk) == norm(topic):
                    k.pop(kk, None)
            lk.write_text(json.dumps(k, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("OK: queued pending with FORCE")
    print("topic:", topic)
    print("wipe_local_knowledge:", wipe)
    print("backup_queue:", brq)
    print("backup_knowledge:", blk)

if __name__ == "__main__":
    main()
