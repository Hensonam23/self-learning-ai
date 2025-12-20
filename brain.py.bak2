#!/usr/bin/env python3
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

KNOWLEDGE_PATH = os.path.join(DATA_DIR, "local_knowledge.json")
RESEARCH_QUEUE_PATH = os.path.join(DATA_DIR, "research_queue.json")

LOW_CONF_THRESHOLD = 0.60
DEFAULT_UNKNOWN_CONFIDENCE = 0.30

# Decay settings
DECAY_ENABLED = True
DECAY_START_DAYS = 180            # after this many days without updates, start decaying
DECAY_PER_30_DAYS = 0.03          # confidence drops by this amount per 30 days beyond DECAY_START_DAYS


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def safe_read_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        print(f"Warning: JSON file is broken: {path}")
        print("Tip: Restore from backup or fix the JSON formatting.")
        return default


def safe_write_json(path: str, obj: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def normalize_topic(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def clamp_conf(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def confidence_tone(conf: float) -> str:
    # Keep it simple and honest
    if conf >= 0.90:
        return "I am very confident about this."
    if conf >= 0.75:
        return "I am fairly confident about this."
    if conf >= 0.60:
        return "I am somewhat confident, but it may be missing details."
    if conf >= 0.40:
        return "Low confidence. This is my current understanding and it may be incomplete."
    return "Very low confidence. Treat this as a rough draft and correct me."


def apply_confidence_decay(entry: Dict[str, Any]) -> Dict[str, Any]:
    if not DECAY_ENABLED:
        return entry

    last_updated = entry.get("last_updated")
    if not last_updated:
        return entry

    try:
        last_dt = datetime.strptime(last_updated, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        return entry

    days_old = (datetime.now(timezone.utc) - last_dt).days
    if days_old <= DECAY_START_DAYS:
        return entry

    extra_days = days_old - DECAY_START_DAYS
    steps = extra_days / 30.0
    decay_amount = steps * DECAY_PER_30_DAYS

    conf = float(entry.get("confidence", 0.0))
    new_conf = clamp_conf(conf - decay_amount)
    if new_conf != conf:
        entry["confidence"] = new_conf
        entry["decayed"] = True
    return entry


def load_knowledge() -> Dict[str, Dict[str, Any]]:
    os.makedirs(DATA_DIR, exist_ok=True)
    data = safe_read_json(KNOWLEDGE_PATH, {})
    if not isinstance(data, dict):
        data = {}

    # Normalize keys and apply decay
    normalized: Dict[str, Dict[str, Any]] = {}
    for k, v in data.items():
        if not isinstance(v, dict):
            continue
        nk = normalize_topic(k)
        v = apply_confidence_decay(v)
        normalized[nk] = v

    # Write back if keys changed (keeps file clean)
    safe_write_json(KNOWLEDGE_PATH, normalized)
    return normalized


def save_knowledge(knowledge: Dict[str, Dict[str, Any]]) -> None:
    safe_write_json(KNOWLEDGE_PATH, knowledge)


def load_queue() -> List[Dict[str, Any]]:
    os.makedirs(DATA_DIR, exist_ok=True)
    q = safe_read_json(RESEARCH_QUEUE_PATH, [])
    if not isinstance(q, list):
        q = []
    return q


def save_queue(queue: List[Dict[str, Any]]) -> None:
    safe_write_json(RESEARCH_QUEUE_PATH, queue)


def queue_contains(queue: List[Dict[str, Any]], topic: str) -> bool:
    nt = normalize_topic(topic)
    for item in queue:
        if normalize_topic(str(item.get("topic", ""))) == nt and item.get("status") in ("pending", "in_progress"):
            return True
    return False


def enqueue_research(queue: List[Dict[str, Any]], topic: str, reason: str, confidence: float) -> bool:
    if queue_contains(queue, topic):
        return False

    queue.append({
        "topic": normalize_topic(topic),
        "reason": reason,
        "requested_on": now_utc_iso(),
        "status": "pending",
        "current_confidence": round(float(confidence), 4),
    })
    return True


def format_answer(answer: str, conf: float) -> str:
    tone = confidence_tone(conf)
    return f"{answer}\n\n{tone} (confidence: {conf:.2f})"


def get_entry(knowledge: Dict[str, Dict[str, Any]], topic: str) -> Optional[Dict[str, Any]]:
    return knowledge.get(normalize_topic(topic))


def set_entry(
    knowledge: Dict[str, Dict[str, Any]],
    topic: str,
    answer: str,
    confidence: float,
    source: str,
    notes: str = ""
) -> None:
    nt = normalize_topic(topic)
    knowledge[nt] = {
        "answer": answer.strip(),
        "confidence": clamp_conf(float(confidence)),
        "source": source,
        "last_updated": now_utc_iso(),
        "notes": notes.strip()
    }


def bump_confidence(old_conf: float, bump: float) -> float:
    return clamp_conf(old_conf + bump)


HELP_TEXT = """
Commands you can use:

/help
/show <topic>
/list                (shows top topics by confidence)
/teach <topic> | <answer>
/rate <topic> | <0.0-1.0>
/forget <topic>
/queue               (shows pending research topics)
/exit

Normal usage:
Just ask a question like usual. If the answer is missing or low confidence,
the topic will be added to research_queue.json automatically.
""".strip()


def parse_pipe_command(line: str) -> Tuple[str, Optional[str], Optional[str]]:
    # returns (cmd, left, right)
    # Example: "/teach osi model | <answer>"
    parts = line.strip().split(" ", 1)
    cmd = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    if "|" in rest:
        left, right = rest.split("|", 1)
        return cmd, left.strip(), right.strip()
    return cmd, rest.strip() if rest else None, None


def show_queue(queue: List[Dict[str, Any]]) -> str:
    pending = [x for x in queue if x.get("status") in ("pending", "in_progress")]
    if not pending:
        return "Research queue is clear."

    lines = ["Pending research topics:"]
    for i, item in enumerate(pending, 1):
        t = item.get("topic", "")
        r = item.get("reason", "")
        c = item.get("current_confidence", "")
        lines.append(f"{i}. {t} (confidence: {c}) reason: {r}")
    return "\n".join(lines)


def list_topics(knowledge: Dict[str, Dict[str, Any]]) -> str:
    if not knowledge:
        return "No taught topics yet."
    items = []
    for topic, entry in knowledge.items():
        conf = float(entry.get("confidence", 0.0))
        items.append((conf, topic))
    items.sort(reverse=True)

    lines = ["Top topics by confidence:"]
    for conf, topic in items[:25]:
        lines.append(f"- {topic} ({conf:.2f})")
    return "\n".join(lines)


def main() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)

    knowledge = load_knowledge()
    queue = load_queue()

    print("Machine Spirit brain online. Type /help for commands. Ctrl+C to exit.")

    while True:
        try:
            user = input("> ").strip()
        except KeyboardInterrupt:
            print("\nShutting down.")
            break

        if not user:
            continue

        # Commands
        if user.startswith("/"):
            cmd, left, right = parse_pipe_command(user)

            if cmd in ("/help",):
                print(HELP_TEXT)
                continue

            if cmd in ("/exit",):
                print("Shutting down.")
                break

            if cmd == "/show":
                if not left:
                    print("Usage: /show <topic>")
                    continue
                entry = get_entry(knowledge, left)
                if not entry:
                    print("No entry for that topic yet.")
                    continue
                print(json.dumps({normalize_topic(left): entry}, indent=2, ensure_ascii=False))
                continue

            if cmd == "/list":
                print(list_topics(knowledge))
                continue

            if cmd == "/queue":
                print(show_queue(queue))
                continue

            if cmd == "/forget":
                if not left:
                    print("Usage: /forget <topic>")
                    continue
                nt = normalize_topic(left)
                if nt in knowledge:
                    del knowledge[nt]
                    save_knowledge(knowledge)
                    print(f"Deleted topic: {nt}")
                else:
                    print("Topic not found.")
                continue

            if cmd == "/rate":
                if not left or right is None:
                    print("Usage: /rate <topic> | <0.0-1.0>")
                    continue
                try:
                    val = float(right)
                except ValueError:
                    print("Rating must be a number like 0.75")
                    continue

                entry = get_entry(knowledge, left)
                if not entry:
                    print("No entry yet for that topic. Teach it first.")
                    continue

                entry["confidence"] = clamp_conf(val)
                entry["last_updated"] = now_utc_iso()
                entry["source"] = entry.get("source", "user_taught")
                knowledge[normalize_topic(left)] = entry
                save_knowledge(knowledge)
                print(f"Updated confidence for '{normalize_topic(left)}' to {entry['confidence']:.2f}")
                continue

            if cmd == "/teach":
                if not left or right is None:
                    print("Usage: /teach <topic> | <answer>")
                    continue

                # If already exists, we treat it as an improvement
                existing = get_entry(knowledge, left)
                if existing:
                    old_conf = float(existing.get("confidence", 0.0))
                    # If user is re-teaching, bump slightly
                    new_conf = bump_confidence(old_conf, 0.05)
                    set_entry(
                        knowledge,
                        left,
                        right,
                        new_conf,
                        source="user_taught",
                        notes="Updated by user re-teach"
                    )
                    save_knowledge(knowledge)
                    print(f"Updated taught answer for '{normalize_topic(left)}' (confidence now {new_conf:.2f})")
                else:
                    set_entry(
                        knowledge,
                        left,
                        right,
                        0.75,
                        source="user_taught",
                        notes="Initial user taught answer"
                    )
                    save_knowledge(knowledge)
                    print(f"Saved taught answer for '{normalize_topic(left)}' (confidence 0.75)")
                continue

            print("Unknown command. Type /help")
            continue

        # Normal question flow
        topic = normalize_topic(user)
        entry = get_entry(knowledge, topic)

        if entry:
            answer = str(entry.get("answer", "")).strip()
            conf = float(entry.get("confidence", 0.0))
            print(format_answer(answer, conf))

            # Auto queue research if confidence is low
            if conf < LOW_CONF_THRESHOLD:
                added = enqueue_research(
                    queue,
                    topic,
                    reason="Answer exists but confidence is low",
                    confidence=conf
                )
                if added:
                    save_queue(queue)
            continue

        # Unknown topic
        unknown_text = (
            "I do not have a taught answer for that yet.\n"
            "If you want to teach me, use:\n"
            "/teach <topic> | <your answer>\n"
            "If I should research it later, I will queue it."
        )
        conf = DEFAULT_UNKNOWN_CONFIDENCE
        print(format_answer(unknown_text, conf))

        added = enqueue_research(
            queue,
            topic,
            reason="No taught answer yet",
            confidence=conf
        )
        if added:
            save_queue(queue)


if __name__ == "__main__":
    main()
