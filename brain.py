#!/usr/bin/env python3
# Machine Spirit Brain (local-first, self-learning helper)
# NOTE: This file is meant to be replaced as a whole (rm -> nano -> paste).
# No partial patching.

import os
import re
import json
import time
import shutil
import datetime
from typing import Dict, Any, List, Tuple, Optional

# ----------------------------
# Paths / folders
# ----------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

KNOWLEDGE_PATH = os.path.join(DATA_DIR, "local_knowledge.json")
ALIASES_PATH = os.path.join(DATA_DIR, "topic_aliases.json")
RESEARCH_QUEUE_PATH = os.path.join(DATA_DIR, "research_queue.json")
RESEARCH_NOTES_DIR = os.path.join(DATA_DIR, "research_notes")
EXPORTS_DIR = os.path.join(DATA_DIR, "exports")

BACKUP_DIR = os.path.join(DATA_DIR, "backups")

# Optional state file (if you’re using auto-ingest)
AUTO_INGEST_STATE_PATH = os.path.join(DATA_DIR, "auto_ingest_state.json")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(RESEARCH_NOTES_DIR, exist_ok=True)
os.makedirs(EXPORTS_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

# ----------------------------
# Backups / timer
# ----------------------------

LAST_BACKUP_TIME = 0
BACKUP_INTERVAL = 300  # seconds (5 minutes)

FILES_TO_BACKUP = [
    KNOWLEDGE_PATH,
    ALIASES_PATH,
    RESEARCH_QUEUE_PATH,
    AUTO_INGEST_STATE_PATH,
]

def backup_files_if_needed():
    """
    Every BACKUP_INTERVAL seconds, copy key JSON files to data/backups with timestamp.
    """
    global LAST_BACKUP_TIME
    now = time.time()
    if now - LAST_BACKUP_TIME < BACKUP_INTERVAL:
        return

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    for fp in FILES_TO_BACKUP:
        if not os.path.exists(fp):
            continue

        base = os.path.basename(fp)
        # keep original filename in backup name, but add timestamp
        backup_name = f"{base}_{timestamp}"
        backup_path = os.path.join(BACKUP_DIR, backup_name)

        try:
            shutil.copy2(fp, backup_path)
        except Exception:
            # we do not crash the brain if backup fails
            pass

    LAST_BACKUP_TIME = now


# ----------------------------
# Config (confidence / learning)
# ----------------------------

CONF_DEFAULT_IF_MISSING = 0.40

# How fast confidence decays over time (per day)
CONF_DECAY_PER_DAY = 0.003

# How much confidence increases when a topic is used (small reinforcement)
CONF_REINFORCE_ON_USE = 0.01

# How much confidence increases when you ingest a research note file
CONF_INGEST_BOOST = 0.10

# Confidence thresholds
CONF_LOW_THRESHOLD = 0.50
CONF_GOOD_THRESHOLD = 0.75

# Queue rules
QUEUE_DONE_STATUSES = {"done", "resolved", "complete"}


# ----------------------------
# JSON helpers
# ----------------------------

def _safe_read_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _safe_write_json(path: str, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

def load_json_dict(path: str) -> dict:
    raw = _safe_read_json(path, {})
    return raw if isinstance(raw, dict) else {}

def save_json_dict(path: str, data: dict) -> None:
    _safe_write_json(path, data)

def load_json_list(path: str) -> list:
    raw = _safe_read_json(path, [])
    return raw if isinstance(raw, list) else []

def save_json_list(path: str, data: list) -> None:
    _safe_write_json(path, data)


# ----------------------------
# Normalize / alias resolution
# ----------------------------

def normalize_topic(topic: str) -> str:
    return (topic or "").strip().lower()

def load_aliases() -> dict:
    return load_json_dict(ALIASES_PATH)

def save_aliases(aliases: dict) -> None:
    save_json_dict(ALIASES_PATH, aliases)

def resolve_topic(topic: str, aliases: dict) -> str:
    """
    Follow alias chain until it resolves to canonical topic.
    Prevent loops.
    """
    t = normalize_topic(topic)
    seen = set()
    while t in aliases and t not in seen:
        seen.add(t)
        t = normalize_topic(aliases.get(t, t))
    return t

def alias_suggestion_for(user_text: str, base: str, aliases: dict) -> str:
    """
    If user text looks like an alias for a known base term, suggest it.
    """
    raw = normalize_topic(user_text)
    if not raw or not base:
        return ""
    if raw in aliases:
        return ""
    return f"Suggestion: /alias {raw} | {base}"


# ----------------------------
# Knowledge + confidence helpers
# ----------------------------

def load_knowledge() -> dict:
    return load_json_dict(KNOWLEDGE_PATH)

def save_knowledge(knowledge: dict) -> None:
    save_json_dict(KNOWLEDGE_PATH, knowledge)

def ensure_entry_schema(entry: dict) -> dict:
    """
    Make sure an entry has the keys we rely on.
    """
    if not isinstance(entry, dict):
        entry = {"answer": str(entry)}

    if "answer" not in entry:
        entry["answer"] = ""

    if "confidence" not in entry or not isinstance(entry["confidence"], (int, float)):
        entry["confidence"] = float(CONF_DEFAULT_IF_MISSING)

    if "last_updated" not in entry:
        entry["last_updated"] = datetime.date.today().isoformat()

    if "notes" not in entry:
        entry["notes"] = ""

    return entry

def _days_since(date_str: str) -> int:
    try:
        d = datetime.date.fromisoformat(date_str)
        return max(0, (datetime.date.today() - d).days)
    except Exception:
        return 0

def apply_confidence_decay(entry: dict) -> dict:
    entry = ensure_entry_schema(entry)
    days_old = _days_since(entry.get("last_updated", ""))
    if days_old <= 0:
        return entry

    decayed = float(entry["confidence"]) - (days_old * CONF_DECAY_PER_DAY)
    # clamp
    if decayed < 0.05:
        decayed = 0.05
    if decayed > 0.99:
        decayed = 0.99

    entry["confidence"] = round(float(decayed), 4)
    return entry

def reinforce_confidence(entry: dict) -> dict:
    entry = ensure_entry_schema(entry)
    boosted = float(entry["confidence"]) + float(CONF_REINFORCE_ON_USE)
    if boosted > 0.99:
        boosted = 0.99
    entry["confidence"] = round(float(boosted), 4)
    # last_updated = “used today” so decay doesn’t punish it immediately
    entry["last_updated"] = datetime.date.today().isoformat()
    return entry

def promote_confidence(entry: dict, amount: float) -> dict:
    entry = ensure_entry_schema(entry)
    boosted = float(entry.get("confidence", CONF_DEFAULT_IF_MISSING)) + float(amount)
    if boosted > 0.99:
        boosted = 0.99
    if boosted < 0.05:
        boosted = 0.05
    entry["confidence"] = round(float(boosted), 4)
    entry["last_updated"] = datetime.date.today().isoformat()
    return entry


# ----------------------------
# Research queue
# ----------------------------

def load_research_queue() -> list:
    return load_json_list(RESEARCH_QUEUE_PATH)

def save_research_queue(queue: list) -> None:
    save_json_list(RESEARCH_QUEUE_PATH, queue)

def _topic_to_note_filename(topic: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9]+", "_", normalize_topic(topic)).strip("_")
    if not safe:
        safe = "topic"
    return safe + ".txt"

def enqueue_research(queue: list, topic: str, reason: str, current_confidence: float) -> list:
    """
    Add to queue if not already present and not already done.
    """
    t = normalize_topic(topic)
    if not t:
        return queue

    for item in queue:
        if normalize_topic(item.get("topic", "")) == t:
            # already exists
            return queue

    note_file = os.path.join(RESEARCH_NOTES_DIR, _topic_to_note_filename(t))
    queue.append({
        "topic": t,
        "reason": reason,
        "requested_on": datetime.date.today().isoformat(),
        "status": "pending",
        "current_confidence": round(float(current_confidence), 4),
        "worker_note": f"Missing research note file: {note_file}"
    })
    return queue

def pending_queue(queue: list) -> list:
    out = []
    for item in queue:
        status = str(item.get("status", "pending")).lower().strip()
        if status not in QUEUE_DONE_STATUSES:
            out.append(item)
    return out

def mark_queue_done(queue: list, topic: str, note: str = "") -> list:
    t = normalize_topic(topic)
    for item in queue:
        if normalize_topic(item.get("topic", "")) == t:
            item["status"] = "done"
            item["completed_on"] = datetime.date.today().isoformat()
            if note:
                item["worker_note"] = note
            return queue
    return queue


# ----------------------------
# Research note ingest
# ----------------------------

def load_auto_ingest_state() -> dict:
    """
    Tracks which note files were ingested already (so we don’t re-ingest forever).
    If you don’t want this feature, you can ignore it.
    """
    state = load_json_dict(AUTO_INGEST_STATE_PATH)
    if "ingested_files" not in state or not isinstance(state["ingested_files"], dict):
        state["ingested_files"] = {}
    return state

def save_auto_ingest_state(state: dict) -> None:
    save_json_dict(AUTO_INGEST_STATE_PATH, state)

def ingest_notes_into_knowledge(knowledge: dict, research_queue: list) -> Tuple[dict, list, dict]:
    """
    Reads .txt files from data/research_notes/
    Each file name should match a topic (normalized-ish).
    File content becomes the answer for that topic, and confidence is boosted.
    Also marks the queue item as done if found.
    """
    report = {"ingested": 0, "skipped": 0, "details": []}

    try:
        files = [f for f in os.listdir(RESEARCH_NOTES_DIR) if f.endswith(".txt")]
    except Exception:
        report["details"].append("ERROR: Could not list research_notes directory.")
        return knowledge, research_queue, report

    if not files:
        report["details"].append("No .txt files found in data/research_notes/")
        return knowledge, research_queue, report

    state = load_auto_ingest_state()

    for fname in sorted(files):
        path = os.path.join(RESEARCH_NOTES_DIR, fname)

        # skip if already ingested (based on last modified time)
        try:
            mtime = os.path.getmtime(path)
        except Exception:
            report["skipped"] += 1
            continue

        key_mtime = str(mtime)
        already = state["ingested_files"].get(fname)
        if already == key_mtime:
            report["skipped"] += 1
            continue

        # topic from filename
        topic_guess = normalize_topic(fname[:-4].replace("_", " "))
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
        except Exception:
            report["skipped"] += 1
            continue

        if not content:
            report["skipped"] += 1
            continue

        # write into knowledge
        entry = knowledge.get(topic_guess, {})
        entry = ensure_entry_schema(entry)
        entry["answer"] = content
        entry["notes"] = "Upgraded knowledge using local research note"
        entry = promote_confidence(entry, CONF_INGEST_BOOST)
        knowledge[topic_guess] = entry

        # mark queue done
        research_queue = mark_queue_done(
            research_queue,
            topic_guess,
            note="Upgraded knowledge using local research note"
        )

        # update state
        state["ingested_files"][fname] = key_mtime

        report["ingested"] += 1
        report["details"].append(f"Ingested: {fname} -> topic '{topic_guess}'")

    save_auto_ingest_state(state)
    return knowledge, research_queue, report


# ----------------------------
# Export
# ----------------------------

def export_knowledge_markdown(knowledge: dict, aliases: dict, out_path: str) -> str:
    """
    Write a simple markdown export summary.
    """
    lines = []
    lines.append("# Machine Spirit Knowledge Export")
    lines.append("")
    lines.append(f"- Exported on: `{datetime.datetime.now().isoformat(timespec='seconds')}`")
    lines.append(f"- Topics: `{len(knowledge)}`")
    lines.append(f"- Aliases: `{len(aliases)}`")
    lines.append("")

    # Aliases section
    if aliases:
        lines.append("## Aliases")
        lines.append("")
        for a in sorted(aliases.keys()):
            lines.append(f"- `{a}` -> `{aliases[a]}`")
        lines.append("")

    lines.append("## Topics")
    lines.append("")
    for k in sorted(knowledge.keys()):
        entry = ensure_entry_schema(knowledge[k])
        entry = apply_confidence_decay(entry)
        conf = entry.get("confidence", "")
        lines.append(f"### {k}")
        lines.append("")
        lines.append(f"- Confidence: `{conf}`")
        lines.append("")
        answer = entry.get("answer", "").strip()
        if answer:
            lines.append(answer)
        else:
            lines.append("_No answer stored._")
        lines.append("")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return out_path


# ----------------------------
# /import (bulk load from file)
# ----------------------------

def parse_topic_blocks(text: str):
    """
    Bulk import format:

    Topic:
    answer lines...
    (blank lines ok)

    Next Topic:
    answer...

    Topic header MUST end with ":".
    """
    lines = text.splitlines()
    current_topic = None
    buf = []

    def flush():
        nonlocal current_topic, buf
        if current_topic:
            ans = "\n".join([x.rstrip() for x in buf]).strip()
            if ans:
                yield (current_topic.strip(), ans)
        current_topic = None
        buf = []

    for line in lines:
        stripped = line.strip()
        if stripped.endswith(":") and len(stripped) > 1:
            if current_topic is not None:
                for item in flush():
                    yield item
            current_topic = stripped[:-1]
            buf = []
        else:
            if current_topic is not None:
                buf.append(line)

    if current_topic is not None:
        ans = "\n".join([x.rstrip() for x in buf]).strip()
        if ans:
            yield (current_topic.strip(), ans)

def import_file_into_knowledge(filename: str, knowledge: dict, overwrite: bool = False) -> Tuple[int, int, bool]:
    """
    Returns (imported_count, skipped_count, not_found)
    """
    path = os.path.join(BASE_DIR, filename)
    if not os.path.exists(path):
        return (0, 0, True)

    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    imported = 0
    skipped = 0
    today = datetime.date.today().isoformat()

    for topic, answer in parse_topic_blocks(text):
        key = normalize_topic(topic)
        if not key:
            continue

        if key in knowledge and not overwrite:
            skipped += 1
            continue

        knowledge[key] = {
            "answer": answer,
            "confidence": 0.95,
            "last_updated": today,
            "notes": "Imported from file"
        }
        imported += 1

    return (imported, skipped, False)


# ----------------------------
# Command parsing helpers
# ----------------------------

def split_command(cmdline: str) -> Tuple[str, str]:
    """
    '/teach topic | answer' -> ('/teach', 'topic | answer')
    '/alias a | b' -> ('/alias', 'a | b')
    """
    parts = cmdline.strip().split(maxsplit=1)
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0].strip(), ""
    return parts[0].strip(), parts[1].strip()

def split_pipe_args(rest: str) -> Tuple[str, str]:
    """
    'left | right' -> ('left','right')
    """
    if "|" not in rest:
        return rest.strip(), ""
    left, right = rest.split("|", 1)
    return left.strip(), right.strip()


# ----------------------------
# UI / Help
# ----------------------------

def print_help():
    print("Commands:")
    print("  /help")
    print("  /teach <topic> | <answer>")
    print("  /teachfile <topic> | <path_to_txt>")
    print("  /import <filename>            (bulk import: Topic: blocks)")
    print("  /import --overwrite <filename>")
    print("  /alias <alias> | <topic>")
    print("  /export                        (writes markdown export to data/exports/)")
    print("  /ingest                        (ingest notes from data/research_notes/)")
    print("  /queue                         (show pending research queue)")
    print("  /queue done <topic>            (mark queue item done)")
    print("  /promote <topic> | <amount>    (add confidence)")
    print("  /confidence <topic>            (show confidence)")
    print("  /lowest <n>                    (show lowest confidence topics)")
    print("")
    print("Normal message = ask the brain. Unknown topics get queued for research.")
    print("")


# ----------------------------
# Main loop
# ----------------------------

def main():
    print("Machine Spirit brain online. Type a message, or /help for commands. Ctrl+C to exit.")

    knowledge = load_knowledge()
    aliases = load_aliases()
    research_queue = load_research_queue()

    last_auto_ingest = 0
    AUTO_INGEST_INTERVAL = 120  # seconds

    while True:
        try:
            # background-ish maintenance on each loop
            backup_files_if_needed()

            # optional auto ingest of research notes
            now = time.time()
            if now - last_auto_ingest > AUTO_INGEST_INTERVAL:
                last_auto_ingest = now
                # Only ingest if there are pending items or notes exist
                knowledge, research_queue, report = ingest_notes_into_knowledge(knowledge, research_queue)
                if report.get("ingested", 0) > 0:
                    save_knowledge(knowledge)
                    save_research_queue(research_queue)

            user_input = input("> ").strip()

            if not user_input:
                continue

            # Always keep alias file fresh in case it changed
            aliases = load_aliases()

            # Command mode
            if user_input.startswith("/"):
                cmd, rest = split_command(user_input)

                if cmd in ("/help", "/h", "/?"):
                    print_help()
                    continue

                if cmd == "/ingest":
                    research_queue = load_research_queue()
                    knowledge, research_queue, report = ingest_notes_into_knowledge(knowledge, research_queue)
                    if report.get("ingested", 0) > 0:
                        save_knowledge(knowledge)
                        save_research_queue(research_queue)
                    print(f"Ingest report: ingested={report.get('ingested')} skipped={report.get('skipped')}")
                    for d in report.get("details", [])[:25]:
                        print("-", d)
                    continue

                if cmd == "/queue":
                    research_queue = load_research_queue()
                    if rest.lower().startswith("done "):
                        topic = rest[5:].strip()
                        if not topic:
                            print("Usage: /queue done <topic>")
                            continue
                        research_queue = mark_queue_done(research_queue, topic, note="Marked done by user")
                        save_research_queue(research_queue)
                        print(f"Marked done: {normalize_topic(topic)}")
                        continue

                    pend = pending_queue(research_queue)
                    if not pend:
                        print("No pending research tasks. Queue is clear.")
                        continue
                    print(f"Pending research tasks: {len(pend)}")
                    for item in pend[:25]:
                        t = item.get("topic", "")
                        r = item.get("reason", "")
                        c = item.get("current_confidence", "")
                        print(f"- {t}  (conf={c})  reason={r}")
                    continue

                if cmd == "/export":
                    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                    out_path = os.path.join(EXPORTS_DIR, f"knowledge_export_{timestamp}.md")
                    saved = export_knowledge_markdown(knowledge, aliases, out_path)
                    print(f"Exported: {saved}")
                    continue

                if cmd == "/import":
                    # /import <filename>
                    # /import --overwrite <filename>
                    args = rest.split()
                    overwrite = False

                    if not args:
                        print("Usage: /import <filename> OR /import --overwrite <filename>")
                        continue

                    if args[0] == "--overwrite":
                        overwrite = True
                        args = args[1:]

                    if not args:
                        print("Usage: /import <filename> OR /import --overwrite <filename>")
                        continue

                    filename = " ".join(args).strip()
                    imported, skipped, not_found = import_file_into_knowledge(filename, knowledge, overwrite=overwrite)

                    if not_found:
                        print(f"Import failed: file not found: {filename}")
                        continue

                    save_knowledge(knowledge)
                    print(f"Import complete. Imported: {imported}, Skipped: {skipped}, Overwrite: {overwrite}")
                    continue

                if cmd == "/teach":
                    left, right = split_pipe_args(rest)
                    if not left or not right:
                        print("Usage: /teach <topic> | <answer>")
                        continue

                    topic = normalize_topic(left)
                    answer = right.strip()

                    if not topic:
                        print("Usage: /teach <topic> | <answer>")
                        continue

                    knowledge[topic] = ensure_entry_schema(knowledge.get(topic, {}))
                    knowledge[topic]["answer"] = answer
                    knowledge[topic]["confidence"] = 0.75
                    knowledge[topic]["last_updated"] = datetime.date.today().isoformat()
                    knowledge[topic]["notes"] = "Updated by user re teach"
                    save_knowledge(knowledge)

                    # If it was in queue, mark it done
                    research_queue = load_research_queue()
                    research_queue = mark_queue_done(research_queue, topic, note="Taught by user")
                    save_research_queue(research_queue)

                    print(f"Taught: {topic}")
                    continue

                if cmd == "/teachfile":
                    left, right = split_pipe_args(rest)
                    if not left or not right:
                        print("Usage: /teachfile <topic> | <path_to_txt>")
                        continue

                    topic = normalize_topic(left)
                    fpath = right.strip()

                    if not os.path.isabs(fpath):
                        # allow relative paths from project root
                        fpath = os.path.join(BASE_DIR, fpath)

                    if not os.path.exists(fpath):
                        print(f"File not found: {fpath}")
                        continue

                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            content = f.read().strip()
                    except Exception:
                        print("Could not read that file.")
                        continue

                    if not content:
                        print("That file is empty.")
                        continue

                    knowledge[topic] = ensure_entry_schema(knowledge.get(topic, {}))
                    knowledge[topic]["answer"] = content
                    knowledge[topic]["confidence"] = 0.80
                    knowledge[topic]["last_updated"] = datetime.date.today().isoformat()
                    knowledge[topic]["notes"] = "Updated by user re teachfile"
                    save_knowledge(knowledge)

                    # mark done in queue if exists
                    research_queue = load_research_queue()
                    research_queue = mark_queue_done(research_queue, topic, note="Taught by user via teachfile")
                    save_research_queue(research_queue)

                    print(f"Taught from file: {topic}")
                    continue

                if cmd == "/alias":
                    left, right = split_pipe_args(rest)
                    if not left or not right:
                        print("Usage: /alias <alias> | <topic>")
                        continue

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

                if cmd == "/promote":
                    left, right = split_pipe_args(rest)
                    topic_text = left.strip()
                    amount_text = right.strip()

                    if not topic_text or not amount_text:
                        print("Usage: /promote <topic> | <amount>")
                        continue

                    try:
                        amount = float(amount_text)
                    except Exception:
                        print("Amount must be a number (example: 0.10).")
                        continue

                    topic = resolve_topic(topic_text, aliases)
                    if topic not in knowledge:
                        print("No entry yet for that topic. Teach it first.")
                        continue

                    knowledge[topic] = apply_confidence_decay(knowledge[topic])
                    knowledge[topic] = promote_confidence(knowledge[topic], amount)
                    save_knowledge(knowledge)

                    print(f"Promoted: {topic}  new_conf={knowledge[topic].get('confidence')}")
                    continue

                if cmd == "/confidence":
                    topic = resolve_topic(rest, aliases)
                    if not topic:
                        print("Usage: /confidence <topic>")
                        continue
                    if topic not in knowledge:
                        print("No entry yet for that topic.")
                        continue
                    entry = apply_confidence_decay(knowledge[topic])
                    knowledge[topic] = entry
                    save_knowledge(knowledge)
                    print(f"{topic} confidence: {entry.get('confidence')}")
                    continue

                if cmd == "/lowest":
                    try:
                        n = int(rest.strip() or "10")
                    except Exception:
                        n = 10

                    items = []
                    for k, v in knowledge.items():
                        if not isinstance(v, dict):
                            continue
                        vv = apply_confidence_decay(v)
                        knowledge[k] = vv
                        items.append((k, float(vv.get("confidence", 0.0))))
                    save_knowledge(knowledge)

                    items.sort(key=lambda x: x[1])
                    print(f"Lowest confidence topics (top {n}):")
                    for k, c in items[:max(1, n)]:
                        print(f"- {k}: {c}")
                    continue

                print("Unknown command. Type /help")
                continue

            # Normal question mode
            resolved = resolve_topic(user_input, aliases)

            if resolved in knowledge:
                # decay + reinforce
                knowledge[resolved] = apply_confidence_decay(knowledge[resolved])
                knowledge[resolved] = reinforce_confidence(knowledge[resolved])
                save_knowledge(knowledge)

                entry = knowledge[resolved]
                answer = str(entry.get("answer", "")).strip()
                conf = float(entry.get("confidence", CONF_DEFAULT_IF_MISSING))

                if not answer:
                    print("Machine Spirit: I have an entry for that, but it has no answer stored yet.")
                else:
                    # If confidence is low, add a gentle warning
                    if conf < CONF_LOW_THRESHOLD:
                        print("Machine Spirit:", answer)
                        print(f"(Note: confidence is low: {conf}. If this is weak, teach me with /teach.)")
                    else:
                        print("Machine Spirit:", answer)

                # Suggest alias mapping when user typed something different than canonical
                base = resolved
                if normalize_topic(user_input) != base:
                    suggestion = alias_suggestion_for(user_input, base, aliases)
                    if suggestion:
                        print(suggestion)

                continue

            # Unknown topic: queue it for research
            # Decide reason and current confidence
            reason = "No taught answer yet"
            current_confidence = 0.30

            research_queue = load_research_queue()
            research_queue = enqueue_research(research_queue, resolved, reason, current_confidence)
            save_research_queue(research_queue)

            print(
                "Machine Spirit: I do not have a taught answer for that yet. "
                "If my reply is wrong or weak, correct me in your own words and I will remember it. "
                "My analysis may be incomplete. If this seems wrong, correct me and I will update my understanding. "
                "I have also marked this topic for deeper research so I can improve my answer over time."
            )

        except KeyboardInterrupt:
            print("\nShutting down.")
            break


if __name__ == "__main__":
    main()
