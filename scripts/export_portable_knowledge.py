#!/usr/bin/env python3
import argparse, json, re, sys
from pathlib import Path
from datetime import datetime, timezone

def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))

def save_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

def normalize_topic(t: str) -> str:
    return (t or "").strip().lower()

def obj_to_entries(obj):
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            topic = normalize_topic(str(k))
            entry = dict(v) if isinstance(v, dict) else {"value": v}
            entry.setdefault("topic", topic)
            out.append((topic, entry))
    elif isinstance(obj, list):
        for it in obj:
            if not isinstance(it, dict):
                continue
            topic = normalize_topic(it.get("topic", ""))
            entry = dict(it)
            entry["topic"] = topic
            out.append((topic, entry))
    else:
        raise ValueError("Unsupported JSON structure (expected dict or list).")
    return out

def entries_to_dict(entries):
    out = {}
    for topic, entry in entries:
        e = dict(entry)
        e.pop("topic", None)
        out[topic] = e
    return out

def load_rules(rules_path: Path):
    rules = {"deny_topic_contains": [], "deny_text_regex": [], "scrub_text_regex": [], "allow_topic_exact": []}
    if not rules_path.exists():
        return rules
    r = load_json(rules_path)
    for k in rules.keys():
        if k in r and isinstance(r[k], list):
            rules[k] = r[k]
    return rules

def scrub_text(text: str, scrub_rules: list):
    s = text or ""
    for it in scrub_rules:
        if not isinstance(it, dict):
            continue
        pat = it.get("pattern")
        rep = it.get("replacement", "")
        if not pat:
            continue
        try:
            s = re.sub(pat, rep, s)
        except re.error:
            continue
    return s

def looks_junk_topic(topic: str) -> bool:
    if not topic:
        return True
    if '"text"' in topic or topic.startswith("{") and topic.endswith("}"):
        return True
    return False

def looks_sensitive(topic: str, entry: dict, rules: dict):
    allow_exact = set(normalize_topic(x) for x in rules.get("allow_topic_exact", []))
    if topic in allow_exact:
        return (False, "allow_topic_exact")

    deny_contains = [normalize_topic(x) for x in rules.get("deny_topic_contains", [])]

    ans = str(entry.get("answer") or "")
    srcs = entry.get("sources") or entry.get("source_urls") or entry.get("source_url") or ""
    if isinstance(srcs, list):
        srcs_txt = " ".join(str(x) for x in srcs)
    else:
        srcs_txt = str(srcs)

    blob = "\n".join([topic, ans, srcs_txt])
    low = blob.lower()

    for needle in deny_contains:
        if needle and needle in low:
            return (True, f"deny_topic_contains:{needle}")

    for pat in rules.get("deny_text_regex", []):
        try:
            if re.search(pat, blob):
                return (True, f"deny_text_regex:{pat}")
        except re.error:
            continue

    return (False, "ok")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="data/local_knowledge.json")
    ap.add_argument("--out", dest="out", default="knowledge/portable_local_knowledge.json")
    ap.add_argument("--manifest", dest="manifest", default="knowledge/portable_manifest.json")
    ap.add_argument("--rules", dest="rules", default="knowledge/privacy_rules.json")
    args = ap.parse_args()

    inp = Path(args.inp)
    out = Path(args.out)
    manifest = Path(args.manifest)
    rules_path = Path(args.rules)

    if not inp.exists():
        print(f"ERROR: input not found: {inp}")
        sys.exit(1)

    rules = load_rules(rules_path)
    scrub_rules = rules.get("scrub_text_regex", [])

    obj = load_json(inp)
    entries = obj_to_entries(obj)

    kept = []
    removed = []
    empty = 0

    for topic, entry in entries:
        if not topic:
            empty += 1
            continue

        if looks_junk_topic(topic):
            removed.append({"topic": topic, "reason": "junk_topic_json"})
            continue

        # SCRUB first
        e = dict(entry)
        if "answer" in e:
            e["answer"] = scrub_text(str(e.get("answer") or ""), scrub_rules)

        for k in ("source_url", "source_urls"):
            if k in e:
                e[k] = scrub_text(str(e.get(k) or ""), scrub_rules)

        if "sources" in e and isinstance(e["sources"], list):
            e["sources"] = [scrub_text(str(x), scrub_rules) for x in e["sources"]]

        # Now decide sensitive vs ok
        sensitive, reason = looks_sensitive(topic, e, rules)
        if sensitive:
            removed.append({"topic": topic, "reason": reason})
            continue

        # Keep only stable fields
        clean = {}
        for k in ("answer", "confidence", "sources", "source_url", "source_urls", "updated_on", "created_on"):
            if k in e:
                clean[k] = e[k]

        kept.append((topic, clean))

    kept_dict = entries_to_dict(kept)
    meta = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source_file": str(inp),
        "rules_file": str(rules_path),
        "kept_count": len(kept_dict),
        "removed_count": len(removed),
        "empty_topic_skipped": empty
    }

    save_json(out, kept_dict)
    save_json(manifest, {"meta": meta, "removed": removed})

    print("OK: wrote portable knowledge pack:", out)
    print("OK: wrote manifest:", manifest)
    print("Summary:", json.dumps(meta, indent=2))

if __name__ == "__main__":
    main()
