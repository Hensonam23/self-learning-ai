#!/usr/bin/env python3
import argparse, json, sys
from pathlib import Path
from datetime import datetime, timezone

def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))

def save_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--portable", default="knowledge/portable_local_knowledge.json")
    ap.add_argument("--target", default="data/local_knowledge.json")
    ap.add_argument("--force", action="store_true", help="overwrite target completely")
    args = ap.parse_args()

    portable = Path(args.portable)
    target = Path(args.target)

    if not portable.exists():
        print(f"ERROR: portable pack not found: {portable}")
        sys.exit(1)

    p_obj = load_json(portable)
    if not isinstance(p_obj, dict):
        print("ERROR: portable pack must be a dict {topic: entry}")
        sys.exit(2)

    if args.force or not target.exists():
        save_json(target, p_obj)
        print(f"OK: wrote target from portable ({'force' if args.force else 'new'}): {target}")
        return

    t_obj = load_json(target)
    if not isinstance(t_obj, dict):
        print("ERROR: target local_knowledge.json is not a dict. Refusing to merge.")
        print("Tip: run with --force if you want to replace it.")
        sys.exit(3)

    added = 0
    upgraded = 0

    for topic, entry in p_obj.items():
        topic_l = (topic or "").strip().lower()
        if not topic_l:
            continue

        if topic_l not in t_obj:
            t_obj[topic_l] = entry
            added += 1
            continue

        cur = t_obj.get(topic_l) or {}
        if not isinstance(cur, dict):
            continue

        cur_conf = float(cur.get("confidence", 0.0) or 0.0)
        new_conf = float(entry.get("confidence", 0.0) or 0.0)

        cur_ans = cur.get("answer") or ""
        new_ans = entry.get("answer") or ""

        changed = False
        if (not cur_ans) and new_ans:
            cur["answer"] = new_ans
            changed = True

        if new_conf > cur_conf:
            cur["confidence"] = new_conf
            if entry.get("sources") and not cur.get("sources"):
                cur["sources"] = entry["sources"]
            changed = True

        if changed:
            t_obj[topic_l] = cur
            upgraded += 1

    t_obj.setdefault("_portable_bootstrap", {})
    t_obj["_portable_bootstrap"]["last_bootstrap_utc"] = datetime.now(timezone.utc).isoformat()
    t_obj["_portable_bootstrap"]["added"] = added
    t_obj["_portable_bootstrap"]["upgraded"] = upgraded

    save_json(target, t_obj)
    print(f"OK: merged portable knowledge into target: {target}")
    print(f"added={added} upgraded={upgraded}")

if __name__ == "__main__":
    main()
