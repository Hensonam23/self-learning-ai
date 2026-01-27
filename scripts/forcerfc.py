#!/usr/bin/env python3
import sys, json
from pathlib import Path

OVERRIDE = Path("data/forced_rfc_map.json")

def load():
    if not OVERRIDE.exists():
        return {}
    txt = OVERRIDE.read_text(encoding="utf-8", errors="replace").strip()
    if not txt:
        return {}
    try:
        obj = json.loads(txt)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}

def save(d):
    OVERRIDE.parent.mkdir(parents=True, exist_ok=True)
    OVERRIDE.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

def die(msg=""):
    if msg:
        print(msg)
    print('Usage:\n  scripts/forcerfc.py list\n  scripts/forcerfc.py add "keyword" "url"\n  scripts/forcerfc.py del "keyword"\n  scripts/forcerfc.py clear')
    sys.exit(1)

def main():
    if len(sys.argv) < 2:
        die()

    cmd = sys.argv[1].lower().strip()
    d = load()

    if cmd == "list":
        print(json.dumps(d, indent=2, ensure_ascii=False))
        return

    if cmd == "add":
        if len(sys.argv) < 4:
            die("ERROR: add needs keyword + url")
        k = (sys.argv[2] or "").strip().lower()
        u = (sys.argv[3] or "").strip()
        if not k or not u:
            die("ERROR: keyword/url cannot be empty")
        d[k] = u
        save(d)
        print(f"OK: set override '{k}' -> '{u}'")
        return

    if cmd == "del":
        if len(sys.argv) < 3:
            die("ERROR: del needs keyword")
        k = (sys.argv[2] or "").strip().lower()
        if k in d:
            d.pop(k, None)
            save(d)
            print(f"OK: removed override '{k}'")
        else:
            print(f"NOTE: override '{k}' not present")
        return

    if cmd == "clear":
        save({})
        print("OK: cleared override file")
        return

    die("ERROR: unknown command")

if __name__ == "__main__":
    main()
