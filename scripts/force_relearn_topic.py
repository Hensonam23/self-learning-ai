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
        die("Usage: scripts/force_relearn_topic.py \"topic\" [--wipe] [--reason \"text\"]")

    topic = sys.argv[1]
    wipe = "--wipe" in sys.argv

    reason = "FORCE relearn"
    if "--reason" in sys.argv:
        i = sys.argv.index("--reason")
        if i + 1 < len(sys.argv):
            reason = "FORCE " + sys.argv[i + 1].strip()
        else:
            die("ERROR: --reason needs a value")

    now = datetime.datetime.now().isoformat(timespec="seconds")

    rq = Path("data/research_queue.json")
    lk = Path("data/local_knowledge.json")

    brq = backup(rq)
    blk = backup(lk)

    q = load_json(rq, [])
    if not isinstance(q, list):
        die("ERROR: research_queue.json is not a list")

    found = False
    for item in q:
        if norm(item.get("topic")) == norm(topic):
            found = True
            item["status"] = "pending"
            item["reason"] = reason
            item["requested_on"] = now
            item["current_confidence"] = 0.0

            # remove anything cooldown/attempt related
            for k in list(item.keys()):
                lk2 = k.lower()
                if "cooldown" in lk2 or "attempt" in lk2 or lk2 in {"completed_on", "worker_note", "chosen_url", "sources"}:
                    item.pop(k, None)
            break

    if not found:
        q.append({
            "topic": topic,
            "reason": reason,
            "requested_on": now,
            "status": "pending",
            "current_confidence": 0.0
        })

    rq.write_text(json.dumps(q, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    wiped = False
    if wipe and lk.exists():
        k = load_json(lk, {})
        if isinstance(k, dict):
            # remove exact + case-insensitive key
            if topic in k:
                k.pop(topic, None); wiped = True
            for kk in list(k.keys()):
                if isinstance(kk, str) and norm(kk) == norm(topic):
                    k.pop(kk, None); wiped = True
            lk.write_text(json.dumps(k, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("OK: queued pending with FORCE")
    print("topic:", topic)
    print("wipe_local_knowledge:", wiped)
    if brq: print("backup_queue:", brq)
    if blk: print("backup_knowledge:", blk)

if __name__ == "__main__":
    main()
