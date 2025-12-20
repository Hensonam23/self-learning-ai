#!/usr/bin/env python3
import os
import sys
import json
import time
import shutil
import difflib
import datetime
from typing import Dict, Any, Tuple, List, Optional

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

KNOWLEDGE_PATH = os.path.join(DATA_DIR, "local_knowledge.json")
ALIAS_PATH = os.path.join(DATA_DIR, "alias_map.json")

RESEARCH_QUEUE_PATH = os.path.join(DATA_DIR, "research_queue.json")
RESEARCH_NOTES_DIR = os.path.join(DATA_DIR, "research_notes")

EXPORTS_DIR = os.path.join(DATA_DIR, "exports")
BACKUPS_DIR = os.path.join(DATA_DIR, "backups")

AUTO_INGEST_DIR = os.path.join(DATA_DIR, "auto_ingest")
AUTO_INGEST_STATE_PATH = os.path.join(DATA_DIR, "auto_ingest_state.json")

# Auto accept alias rules
# difflib ratio can be low for prefix cases like subnet vs subnetting
AUTO_ALIAS_SCORE_THRESHOLD = 0.92
AUTO_ALIAS_CONFIDENCE_THRESHOLD = 0.70

# Prefix rule for obvious aliases
AUTO_ALIAS_PREFIX_MIN_LEN = 4
AUTO_ALIAS_PREFIX_MAX_EXTRA_CHARS = 8
AUTO_ALIAS_MARGIN_THRESHOLD = 0.15  # best must beat runner-up by this margin

# Backup timer (seconds)
BACKUP_INTERVAL_SECONDS = 10 * 60  # 10 minutes

# Auto ingest scan interval (seconds)
AUTO_INGEST_SCAN_INTERVAL_SECONDS = 20


def ensure_dirs() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(RESEARCH_NOTES_DIR, exist_ok=True)
    os.makedirs(EXPORTS_DIR, exist_ok=True)
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    os.makedirs(AUTO_INGEST_DIR, exist_ok=True)


def now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def load_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, data: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def normalize_topic(s: str) -> str:
    s = s.strip().lower()
    s = " ".join(s.split())
    return s


def safe_print(msg: str) -> None:
    try:
        print(msg)
    except BrokenPipeError:
        pass


def backup_runtime_files() -> Optional[str]:
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(BACKUPS_DIR, ts)
    try:
        os.makedirs(backup_dir, exist_ok=True)
        for p in [KNOWLEDGE_PATH, ALIAS_PATH, RESEARCH_QUEUE_PATH, AUTO_INGEST_STATE_PATH]:
            if os.path.exists(p):
                shutil.copy2(p, os.path.join(backup_dir, os.path.basename(p)))
        return backup_dir
    except Exception as e:
        safe_print(f"Backup failed: {e}")
        return None


def knowledge_get_entry(knowledge: Dict[str, Any], topic: str) -> Optional[Dict[str, Any]]:
    topic_n = normalize_topic(topic)
    return knowledge.get(topic_n)


def knowledge_set_entry(
    knowledge: Dict[str, Any],
    topic: str,
    answer: str,
    confidence: float,
    notes: str = ""
) -> None:
    topic_n = normalize_topic(topic)
    knowledge[topic_n] = {
        "answer": answer.strip(),
        "confidence": float(confidence),
        "updated_on": now_iso(),
        "notes": notes.strip()
    }


def list_topics(knowledge: Dict[str, Any]) -> List[str]:
    return sorted(list(knowledge.keys()))


def get_confidence(knowledge: Dict[str, Any], topic: str) -> float:
    entry = knowledge_get_entry(knowledge, topic)
    if not entry:
        return 0.0
    try:
        return float(entry.get("confidence", 0.0))
    except Exception:
        return 0.0


def set_confidence(knowledge: Dict[str, Any], topic: str, conf: float) -> bool:
    topic_n = normalize_topic(topic)
    if topic_n not in knowledge:
        return False
    knowledge[topic_n]["confidence"] = float(conf)
    knowledge[topic_n]["updated_on"] = now_iso()
    return True


def best_fuzzy_match(query: str, choices: List[str]) -> Tuple[Optional[str], float]:
    q = normalize_topic(query)
    best = None
    best_score = 0.0
    for c in choices:
        score = difflib.SequenceMatcher(None, q, c).ratio()
        if score > best_score:
            best_score = score
            best = c
    return best, best_score


def top_two_matches(query: str, choices: List[str]) -> List[Tuple[str, float]]:
    qn = normalize_topic(query)
    scored = []
    for c in choices:
        scored.append((c, difflib.SequenceMatcher(None, qn, c).ratio()))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:2]


def load_or_init_files() -> Tuple[Dict[str, Any], Dict[str, str], List[Dict[str, Any]], Dict[str, Any]]:
    knowledge = load_json(KNOWLEDGE_PATH, {})
    if not isinstance(knowledge, dict):
        knowledge = {}

    alias_map = load_json(ALIAS_PATH, {})
    if not isinstance(alias_map, dict):
        alias_map = {}

    research_queue = load_json(RESEARCH_QUEUE_PATH, [])
    if not isinstance(research_queue, list):
        research_queue = []

    ingest_state = load_json(AUTO_INGEST_STATE_PATH, {"processed_files": {}, "last_scan": 0})
    if not isinstance(ingest_state, dict):
        ingest_state = {"processed_files": {}, "last_scan": 0}
    if "processed_files" not in ingest_state or not isinstance(ingest_state["processed_files"], dict):
        ingest_state["processed_files"] = {}
    if "last_scan" not in ingest_state:
        ingest_state["last_scan"] = 0

    return knowledge, alias_map, research_queue, ingest_state


def add_to_research_queue(queue: List[Dict[str, Any]], topic: str, reason: str, current_confidence: float) -> None:
    topic_n = normalize_topic(topic)
    for item in queue:
        if normalize_topic(item.get("topic", "")) == topic_n and item.get("status", "") == "pending":
            return
    queue.append({
        "topic": topic_n,
        "reason": reason,
        "requested_on": datetime.date.today().isoformat(),
        "status": "pending",
        "current_confidence": float(current_confidence),
        "worker_note": f"Missing research note file: {os.path.join(RESEARCH_NOTES_DIR, topic_n.replace(' ', '_') + '.txt')}"
    })


def promote_research_note(knowledge: Dict[str, Any], queue: List[Dict[str, Any]], topic: str) -> Tuple[bool, str]:
    topic_n = normalize_topic(topic)
    note_file = os.path.join(RESEARCH_NOTES_DIR, topic_n.replace(" ", "_") + ".txt")
    if not os.path.exists(note_file):
        return False, f"No research note file found: {note_file}"

    with open(note_file, "r", encoding="utf-8") as f:
        note_text = f.read().strip()

    if not note_text:
        return False, "Research note file is empty."

    base_conf = max(get_confidence(knowledge, topic_n), 0.60)
    knowledge_set_entry(knowledge, topic_n, note_text, base_conf, notes="Promoted from research note")

    for item in queue:
        if normalize_topic(item.get("topic", "")) == topic_n and item.get("status", "") == "pending":
            item["status"] = "done"
            item["completed_on"] = datetime.date.today().isoformat()
            item["current_confidence"] = float(base_conf)
            item["worker_note"] = "Upgraded knowledge using local research note"
            break

    return True, f"Promoted research note into knowledge for: {topic_n} (confidence {base_conf:.2f})"


def export_knowledge(knowledge: Dict[str, Any], alias_map: Dict[str, str]) -> str:
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(EXPORTS_DIR, f"export_{ts}.json")
    payload = {
        "exported_on": now_iso(),
        "knowledge": knowledge,
        "alias_map": alias_map
    }
    save_json(out_path, payload)
    return out_path


def parse_teach_payload(text: str) -> Optional[Tuple[str, str]]:
    if "|" not in text:
        return None
    left, right = text.split("|", 1)
    topic = normalize_topic(left)
    answer = right.strip()
    if not topic or not answer:
        return None
    return topic, answer


def teach_from_file(knowledge: Dict[str, Any], filepath: str) -> Tuple[int, int]:
    ok = 0
    bad = 0
    if not os.path.exists(filepath):
        return (0, 1)
    with open(filepath, "r", encoding="utf-8") as f:
        for raw in f.readlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parsed = parse_teach_payload(line)
            if not parsed:
                bad += 1
                continue
            topic, answer = parsed
            knowledge_set_entry(
                knowledge,
                topic,
                answer,
                confidence=max(get_confidence(knowledge, topic), 0.55),
                notes="Taught via teachfile"
            )
            ok += 1
    return ok, bad


def import_json_file(knowledge: Dict[str, Any], alias_map: Dict[str, str], filepath: str) -> Tuple[bool, str]:
    if not os.path.exists(filepath):
        return False, "File not found."

    payload = load_json(filepath, None)
    if payload is None:
        return False, "Could not read JSON."

    if isinstance(payload, dict) and "knowledge" in payload:
        k = payload.get("knowledge", {})
        a = payload.get("alias_map", {})
        if isinstance(k, dict):
            for t, entry in k.items():
                t_n = normalize_topic(t)
                if isinstance(entry, dict) and "answer" in entry:
                    knowledge[t_n] = entry
        if isinstance(a, dict):
            for k_alias, v_topic in a.items():
                alias_map[normalize_topic(k_alias)] = normalize_topic(v_topic)
        return True, "Imported export payload."
    elif isinstance(payload, dict):
        for t, entry in payload.items():
            t_n = normalize_topic(t)
            if isinstance(entry, dict) and "answer" in entry:
                knowledge[t_n] = entry
            elif isinstance(entry, str):
                knowledge_set_entry(
                    knowledge,
                    t_n,
                    entry,
                    confidence=max(get_confidence(knowledge, t_n), 0.50),
                    notes="Imported from json string"
                )
        return True, "Imported topic dictionary."
    else:
        return False, "Unsupported JSON structure."


def import_folder(knowledge: Dict[str, Any], alias_map: Dict[str, str], folder: str) -> Tuple[int, int]:
    if not os.path.isdir(folder):
        return (0, 1)

    ok = 0
    bad = 0
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if os.path.isdir(path):
            continue
        if name.lower().endswith(".json"):
            success, _ = import_json_file(knowledge, alias_map, path)
            ok += 1 if success else 0
            bad += 0 if success else 1
        elif name.lower().endswith(".txt"):
            k_ok, k_bad = teach_from_file(knowledge, path)
            ok += k_ok
            bad += k_bad
    return ok, bad


def ingest_text_to_research_note(topic: str, text: str) -> Tuple[bool, str]:
    topic_n = normalize_topic(topic)
    note_file = os.path.join(RESEARCH_NOTES_DIR, topic_n.replace(" ", "_") + ".txt")
    try:
        with open(note_file, "a", encoding="utf-8") as f:
            if os.path.getsize(note_file) > 0:
                f.write("\n\n")
            f.write(text.strip())
        return True, f"Ingested into: {note_file}"
    except Exception as e:
        return False, f"Ingest failed: {e}"


def auto_ingest_scan(
    knowledge: Dict[str, Any],
    alias_map: Dict[str, str],
    ingest_state: Dict[str, Any]
) -> List[str]:
    processed = ingest_state.get("processed_files", {})
    logs = []

    if not os.path.isdir(AUTO_INGEST_DIR):
        return logs

    for name in sorted(os.listdir(AUTO_INGEST_DIR)):
        path = os.path.join(AUTO_INGEST_DIR, name)
        if os.path.isdir(path):
            continue

        try:
            stat = os.stat(path)
        except Exception:
            continue

        key = os.path.abspath(path)
        mtime = int(stat.st_mtime)

        if key in processed and processed[key] == mtime:
            continue

        if name.lower().endswith(".json"):
            success, msg = import_json_file(knowledge, alias_map, path)
            logs.append(f"auto_ingest: {name}: {msg}")
        elif name.lower().endswith(".txt"):
            ok, bad = teach_from_file(knowledge, path)
            logs.append(f"auto_ingest: {name}: taught ok={ok} bad={bad}")
        else:
            logs.append(f"auto_ingest: {name}: skipped unsupported file type")

        processed[key] = mtime

    ingest_state["processed_files"] = processed
    ingest_state["last_scan"] = int(time.time())
    return logs


def resolve_topic(
    user_input: str,
    knowledge: Dict[str, Any],
    alias_map: Dict[str, str]
) -> Tuple[Optional[str], Optional[str]]:
    t = normalize_topic(user_input)

    if t in knowledge:
        return t, "exact"

    if t in alias_map:
        mapped = normalize_topic(alias_map[t])
        if mapped in knowledge:
            return mapped, "alias"

    return None, None


def is_obvious_prefix_alias(alias_key: str, target_topic: str) -> bool:
    a = normalize_topic(alias_key)
    t = normalize_topic(target_topic)

    if a == t:
        return False
    if len(a) < AUTO_ALIAS_PREFIX_MIN_LEN:
        return False
    if not t.startswith(a):
        return False

    extra = len(t) - len(a)
    if extra <= 0:
        return False
    if extra > AUTO_ALIAS_PREFIX_MAX_EXTRA_CHARS:
        return False

    return True


def auto_accept_alias_if_obvious(
    raw_input: str,
    best_topic: str,
    best_score: float,
    second_score: float,
    knowledge: Dict[str, Any],
    alias_map: Dict[str, str]
) -> Tuple[bool, str]:
    alias_key = normalize_topic(raw_input)
    target_conf = get_confidence(knowledge, best_topic)

    if alias_key in knowledge:
        return False, ""

    if alias_key in alias_map and normalize_topic(alias_map[alias_key]) != best_topic:
        return False, "Alias exists with different target. Not auto accepting."

    if best_score >= AUTO_ALIAS_SCORE_THRESHOLD and target_conf >= AUTO_ALIAS_CONFIDENCE_THRESHOLD:
        alias_map[alias_key] = best_topic
        return True, f"Auto accepted alias: {alias_key} -> {best_topic} (score {best_score:.2f}, confidence {target_conf:.2f})"

    if target_conf >= AUTO_ALIAS_CONFIDENCE_THRESHOLD and is_obvious_prefix_alias(alias_key, best_topic):
        margin = best_score - second_score
        if margin >= AUTO_ALIAS_MARGIN_THRESHOLD:
            alias_map[alias_key] = best_topic
            return True, f"Auto accepted alias: {alias_key} -> {best_topic} (prefix rule, score {best_score:.2f}, margin {margin:.2f}, confidence {target_conf:.2f})"

    return False, ""


def show_help() -> None:
    safe_print("Commands:")
    safe_print("  /help")
    safe_print("  /teach <topic> | <answer>")
    safe_print("  /teachfile <path_to_txt>")
    safe_print("  /import <path_to_json>")
    safe_print("  /importfolder <path_to_folder>")
    safe_print("  /ingest <topic> | <text_to_append_to_research_note>")
    safe_print("  /export")
    safe_print("  /queue")
    safe_print("  /promote <topic>")
    safe_print("  /confidence <topic> | <0.0-1.0>")
    safe_print("  /lowest [n]")
    safe_print("  /alias <alias> | <topic>")
    safe_print("  /aliases [n]")
    safe_print("  /unalias <alias>")
    safe_print("  /suggest <text>")
    safe_print("  /accept")
    safe_print("")
    safe_print("Type a message to ask a topic. Ctrl+C to exit.")


def format_answer(entry: Dict[str, Any]) -> str:
    ans = entry.get("answer", "").strip()
    conf = entry.get("confidence", 0.0)
    return f"{ans}\n\n(confidence: {float(conf):.2f})"


def main() -> None:
    ensure_dirs()
    knowledge, alias_map, research_queue, ingest_state = load_or_init_files()

    last_backup_time = time.time()
    last_ingest_scan_time = 0.0

    last_suggested_alias: Optional[Dict[str, Any]] = None
    last_suggested_list: List[Tuple[str, float]] = []

    safe_print("Machine Spirit brain online. Type a message, or /help for commands. Ctrl+C to exit.")

    while True:
        if time.time() - last_backup_time >= BACKUP_INTERVAL_SECONDS:
            backup_runtime_files()
            last_backup_time = time.time()

        if time.time() - last_ingest_scan_time >= AUTO_INGEST_SCAN_INTERVAL_SECONDS:
            logs = auto_ingest_scan(knowledge, alias_map, ingest_state)
            if logs:
                for line in logs:
                    safe_print(line)
                save_json(KNOWLEDGE_PATH, knowledge)
                save_json(ALIAS_PATH, alias_map)
                save_json(AUTO_INGEST_STATE_PATH, ingest_state)
            last_ingest_scan_time = time.time()

        try:
            user = input("> ").strip()
        except KeyboardInterrupt:
            safe_print("\nShutting down.")
            save_json(KNOWLEDGE_PATH, knowledge)
            save_json(ALIAS_PATH, alias_map)
            save_json(RESEARCH_QUEUE_PATH, research_queue)
            save_json(AUTO_INGEST_STATE_PATH, ingest_state)
            return

        if not user:
            continue

        if user.startswith("/"):
            cmd = user.strip()

            if cmd == "/help":
                show_help()
                continue

            if cmd == "/accept":
                if not last_suggested_alias:
                    safe_print("Nothing to accept yet.")
                    continue
                alias_key = normalize_topic(last_suggested_alias["alias"])
                target = normalize_topic(last_suggested_alias["target"])
                alias_map[alias_key] = target
                save_json(ALIAS_PATH, alias_map)
                safe_print(f"Accepted alias: {alias_key} -> {target}")
                last_suggested_alias = None
                continue

            if cmd.startswith("/suggest"):
                parts = cmd.split(" ", 1)
                if len(parts) < 2 or not parts[1].strip():
                    safe_print("Usage: /suggest <text>")
                    continue
                q = parts[1].strip()
                choices = list_topics(knowledge)
                if not choices:
                    safe_print("No topics exist yet.")
                    continue

                scored = []
                qn = normalize_topic(q)
                for c in choices:
                    scored.append((c, difflib.SequenceMatcher(None, qn, c).ratio()))
                scored.sort(key=lambda x: x[1], reverse=True)
                last_suggested_list = scored[:5]

                safe_print("Suggestions:")
                for t, s in last_suggested_list:
                    safe_print(f"  {t}  (score {s:.2f}, confidence {get_confidence(knowledge, t):.2f})")
                continue

            if cmd.startswith("/teach"):
                if cmd == "/teach":
                    safe_print("Usage: /teach <topic> | <answer>")
                    continue
                payload = cmd[len("/teach"):].strip()
                parsed = parse_teach_payload(payload)
                if not parsed:
                    safe_print("Usage: /teach <topic> | <answer>")
                    continue
                topic, answer = parsed
                new_conf = max(get_confidence(knowledge, topic), 0.55)
                knowledge_set_entry(knowledge, topic, answer, new_conf, notes="Updated by user via teach")
                save_json(KNOWLEDGE_PATH, knowledge)
                safe_print(f"Taught: {topic} (confidence {new_conf:.2f})")
                continue

            if cmd.startswith("/teachfile"):
                parts = cmd.split(" ", 1)
                if len(parts) < 2:
                    safe_print("Usage: /teachfile <path_to_txt>")
                    continue
                path = parts[1].strip()
                ok, bad = teach_from_file(knowledge, path)
                save_json(KNOWLEDGE_PATH, knowledge)
                safe_print(f"Teachfile complete. ok={ok} bad={bad}")
                continue

            if cmd.startswith("/importfolder"):
                parts = cmd.split(" ", 1)
                if len(parts) < 2:
                    safe_print("Usage: /importfolder <path_to_folder>")
                    continue
                folder = parts[1].strip()
                ok, bad = import_folder(knowledge, alias_map, folder)
                save_json(KNOWLEDGE_PATH, knowledge)
                save_json(ALIAS_PATH, alias_map)
                safe_print(f"Importfolder complete. ok={ok} bad={bad}")
                continue

            if cmd.startswith("/import"):
                parts = cmd.split(" ", 1)
                if len(parts) < 2:
                    safe_print("Usage: /import <path_to_json>")
                    continue
                path = parts[1].strip()
                success, msg = import_json_file(knowledge, alias_map, path)
                if success:
                    save_json(KNOWLEDGE_PATH, knowledge)
                    save_json(ALIAS_PATH, alias_map)
                safe_print(msg)
                continue

            if cmd.startswith("/ingest"):
                payload = cmd[len("/ingest"):].strip()
                parsed = parse_teach_payload(payload)
                if not parsed:
                    safe_print("Usage: /ingest <topic> | <text_to_append_to_research_note>")
                    continue
                topic, text = parsed
                ok, msg = ingest_text_to_research_note(topic, text)
                if ok:
                    add_to_research_queue(
                        research_queue,
                        topic,
                        reason="Manual ingest added new research note content",
                        current_confidence=get_confidence(knowledge, topic)
                    )
                    save_json(RESEARCH_QUEUE_PATH, research_queue)
                safe_print(msg)
                continue

            if cmd == "/export":
                out = export_knowledge(knowledge, alias_map)
                safe_print(f"Exported to: {out}")
                continue

            if cmd == "/queue":
                pending = [q for q in research_queue if q.get("status") == "pending"]
                done = [q for q in research_queue if q.get("status") == "done"]
                safe_print(f"Queue: pending={len(pending)} done={len(done)} total={len(research_queue)}")
                if pending:
                    safe_print("Pending:")
                    for item in pending[:20]:
                        safe_print(f"  - {item.get('topic')} (conf {item.get('current_confidence', 0):.2f}) reason: {item.get('reason')}")
                continue

            if cmd.startswith("/promote"):
                parts = cmd.split(" ", 1)
                if len(parts) < 2:
                    safe_print("Usage: /promote <topic>")
                    continue
                topic = parts[1].strip()
                ok, msg = promote_research_note(knowledge, research_queue, topic)
                if ok:
                    save_json(KNOWLEDGE_PATH, knowledge)
                    save_json(RESEARCH_QUEUE_PATH, research_queue)
                safe_print(msg)
                continue

            if cmd.startswith("/confidence"):
                payload = cmd[len("/confidence"):].strip()
                parsed = parse_teach_payload(payload)
                if not parsed:
                    safe_print("Usage: /confidence <topic> | <0.0-1.0>")
                    continue
                topic, conf_str = parsed
                try:
                    conf_val = float(conf_str)
                except Exception:
                    safe_print("Confidence must be a number 0.0 to 1.0")
                    continue
                conf_val = max(0.0, min(1.0, conf_val))
                if not set_confidence(knowledge, topic, conf_val):
                    safe_print("No entry yet for that topic. Teach it first.")
                    continue
                save_json(KNOWLEDGE_PATH, knowledge)
                safe_print(f"Updated confidence: {normalize_topic(topic)} -> {conf_val:.2f}")
                continue

            if cmd.startswith("/lowest"):
                parts = cmd.split(" ", 1)
                n = 10
                if len(parts) == 2 and parts[1].strip():
                    try:
                        n = int(parts[1].strip())
                    except Exception:
                        n = 10
                items = []
                for t in knowledge.keys():
                    items.append((t, get_confidence(knowledge, t)))
                items.sort(key=lambda x: x[1])
                safe_print(f"Lowest confidence topics (top {n}):")
                for t, c in items[:n]:
                    safe_print(f"  {t}  (confidence {c:.2f})")
                continue

            if cmd.startswith("/alias"):
                payload = cmd[len("/alias"):].strip()
                parsed = parse_teach_payload(payload)
                if not parsed:
                    safe_print("Usage: /alias <alias> | <topic>")
                    continue
                alias_key, target = parsed
                if normalize_topic(target) not in knowledge:
                    safe_print("Target topic does not exist in knowledge. Teach it first.")
                    continue
                alias_map[normalize_topic(alias_key)] = normalize_topic(target)
                save_json(ALIAS_PATH, alias_map)
                safe_print(f"Saved alias: {normalize_topic(alias_key)} -> {normalize_topic(target)}")
                continue

            if cmd.startswith("/aliases"):
                parts = cmd.split(" ", 1)
                n = 25
                if len(parts) == 2 and parts[1].strip():
                    try:
                        n = int(parts[1].strip())
                    except Exception:
                        n = 25

                if not alias_map:
                    safe_print("No aliases saved.")
                    continue

                safe_print(f"Aliases (showing up to {n}):")
                count = 0
                for a in sorted(alias_map.keys()):
                    t = normalize_topic(alias_map[a])
                    conf = get_confidence(knowledge, t)
                    safe_print(f"  {a} -> {t}  (confidence {conf:.2f})")
                    count += 1
                    if count >= n:
                        break
                continue

            if cmd.startswith("/unalias"):
                parts = cmd.split(" ", 1)
                if len(parts) < 2 or not parts[1].strip():
                    safe_print("Usage: /unalias <alias>")
                    continue
                akey = normalize_topic(parts[1].strip())
                if akey not in alias_map:
                    safe_print("Alias not found.")
                    continue
                del alias_map[akey]
                save_json(ALIAS_PATH, alias_map)
                safe_print(f"Removed alias: {akey}")
                continue

            safe_print("Unknown command. Type /help")
            continue

        # Normal question flow
        resolved, reason = resolve_topic(user, knowledge, alias_map)
        if resolved and resolved in knowledge:
            safe_print(format_answer(knowledge[resolved]))
            continue

        topics = list_topics(knowledge)
        if topics:
            top2 = top_two_matches(user, topics)
            best = top2[0][0] if len(top2) >= 1 else None
            best_score = top2[0][1] if len(top2) >= 1 else 0.0
            second_score = top2[1][1] if len(top2) >= 2 else 0.0

            if best:
                did_accept, accept_msg = auto_accept_alias_if_obvious(
                    user,
                    best,
                    best_score,
                    second_score,
                    knowledge,
                    alias_map
                )
                if did_accept:
                    save_json(ALIAS_PATH, alias_map)
                    safe_print(accept_msg)
                    safe_print(format_answer(knowledge[best]))
                    continue

                last_suggested_alias = {
                    "alias": user,
                    "target": best,
                    "score": float(best_score)
                }
                safe_print(f"Suggestion: /alias {user} | {best}")
                safe_print("Tip: use /accept to save that alias, or /suggest <text> to see more.")
                add_to_research_queue(research_queue, user, reason="No taught answer yet", current_confidence=0.30)
                save_json(RESEARCH_QUEUE_PATH, research_queue)
                continue

        safe_print("Machine Spirit: I do not have a taught answer for that yet.")
        add_to_research_queue(research_queue, user, reason="No taught answer yet", current_confidence=0.30)
        save_json(RESEARCH_QUEUE_PATH, research_queue)


if __name__ == "__main__":
    main()
