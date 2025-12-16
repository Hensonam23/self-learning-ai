import json
import re
import subprocess
from pathlib import Path
from datetime import datetime

APP_ROOT = Path(__file__).resolve().parent
DATA_DIR = APP_ROOT / "data"

RESEARCH_QUEUE_PATH = DATA_DIR / "research_queue.json"
RESEARCH_NOTES_PATH = DATA_DIR / "research_notes.json"

# Taught knowledge (promoted "approved" answers)
TAUGHT_PATH = DATA_DIR / "taught_knowledge.json"

CONFIDENCE_DIRECT_ANSWER = 0.8
CONFIDENCE_APPROVE_GAIN = 0.3
CONFIDENCE_CORRECT_PENALTY = 0.4

# Auto-promotion rules
PROMOTE_MIN_CONFIDENCE = 0.8
PROMOTE_MIN_APPROVALS = 2

LAST_DRAFT_KEY = None


# --------------------
# File helpers
# --------------------
def load_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds")


def normalize_key(text: str) -> str:
    t = (text or "").lower().strip()
    t = re.sub(r"[?!.]+$", "", t)
    t = re.sub(r"\s+", " ", t)
    return t


def normalize_confidence(value):
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        v = value.lower().strip()
        if v == "low":
            return 0.2
        if v == "medium":
            return 0.5
        if v == "high":
            return 0.8
        try:
            return float(v)
        except Exception:
            return 0.2
    return 0.2


# --------------------
# Stores
# --------------------
def get_notes():
    return load_json(RESEARCH_NOTES_PATH, {"drafts": {}})


def save_notes(notes):
    save_json(RESEARCH_NOTES_PATH, notes)


def get_taught():
    return load_json(TAUGHT_PATH, {"topics": {}})


def save_taught(taught):
    save_json(TAUGHT_PATH, taught)


def find_draft_key_by_topic(notes, topic: str):
    key_topic = normalize_key(topic)
    drafts = notes.get("drafts", {})
    if key_topic in drafts:
        return key_topic
    for k, d in drafts.items():
        if normalize_key(d.get("topic", "")) == key_topic:
            return k
    return None


def question_to_candidate_keys(question: str):
    q = normalize_key(question)
    keys = [q]

    q2 = re.sub(r"^(what is|what are|define|explain|tell me about)\s+", "", q).strip()
    if q2 and q2 != q:
        keys.append(q2)

    q3 = re.sub(r"^(a|an|the)\s+", "", q2).strip()
    if q3 and q3 != q2:
        keys.append(q3)

    parts = q3.split()
    if parts:
        keys.append(parts[-1])

    out = []
    for k in keys:
        if k and k not in out:
            out.append(k)
    return out


# --------------------
# Taught lookup (answers first)
# --------------------
def try_taught_answer(question: str) -> bool:
    taught = get_taught().get("topics", {})
    for key in question_to_candidate_keys(question):
        if key in taught:
            item = taught[key]
            text = (item.get("detailed") or item.get("short") or "").strip()
            if text:
                print("Machine Spirit (answer): " + text)
                return True
    return False


# --------------------
# Draft selection
# --------------------
def choose_output_for_draft(draft):
    conf = normalize_confidence(draft.get("confidence"))
    detailed = (draft.get("detailed") or "").strip()
    short = (draft.get("short") or "").strip()

    if conf >= CONFIDENCE_DIRECT_ANSWER and detailed:
        return "answer", detailed
    return "draft", (detailed or short)


def show_draft_by_key(notes, key):
    global LAST_DRAFT_KEY
    drafts = notes.get("drafts", {})
    if key not in drafts:
        return False

    draft = drafts[key]
    label, text = choose_output_for_draft(draft)
    LAST_DRAFT_KEY = key

    print("Machine Spirit (" + label + "): " + (text or "[empty]"))

    if label != "answer":
        print("Machine Spirit: If you like this, approve it with:")
        print("  approve: " + str(draft.get("topic", "")))
        print("Or view/edit it with:")
        print("  draft: " + str(draft.get("topic", "")))
    return True


def show_best_for_question(question: str):
    notes = get_notes()
    drafts = notes.get("drafts", {})
    if not drafts:
        return False

    is_comparison_question = any(
        w in question.lower() for w in [" vs ", "versus", "difference", "compare"]
    )

    for k in question_to_candidate_keys(question):
        if k in drafts:
            d = drafts[k]
            if d.get("type") == "comparison" and not is_comparison_question:
                continue
            return show_draft_by_key(notes, k)

    core = question_to_candidate_keys(question)[-1]
    matches = []
    for k, d in drafts.items():
        if d.get("type") == "comparison" and not is_comparison_question:
            continue
        topic = (d.get("topic") or "").lower()
        if core and core in topic:
            matches.append((k, d))

    if not matches:
        return False

    if len(matches) == 1:
        return show_draft_by_key(notes, matches[0][0])

    print("Machine Spirit: Multiple drafts might match. Pick one:")
    for i, (k, d) in enumerate(matches, 1):
        print("  " + str(i) + ") " + str(d.get("topic")))
    print("Use: draft: <topic> or approve: <topic>")
    return True


# --------------------
# Research
# --------------------
def queue_research(topic):
    queue = load_json(RESEARCH_QUEUE_PATH, {"queue": []})
    queue["queue"].append({"topic": topic, "queued_at": now_iso()})
    save_json(RESEARCH_QUEUE_PATH, queue)

    subprocess.run(["python3", "research_worker.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# --------------------
# Promotion
# --------------------
def promote_if_ready(draft):
    topic = draft.get("topic", "")
    key = normalize_key(topic)
    conf = normalize_confidence(draft.get("confidence"))
    approvals = int(draft.get("approvals", 0))

    if conf < PROMOTE_MIN_CONFIDENCE:
        return False
    if approvals < PROMOTE_MIN_APPROVALS:
        return False

    detailed = (draft.get("detailed") or "").strip()
    short = (draft.get("short") or "").strip()
    if not (detailed or short):
        return False

    taught = get_taught()
    taught_topics = taught.get("topics", {})

    taught_topics[key] = {
        "topic": topic,
        "short": short,
        "detailed": detailed,
        "promoted_at": now_iso(),
        "from": "drafts",
        "confidence_at_promotion": conf,
        "approvals_at_promotion": approvals,
    }

    taught["topics"] = taught_topics
    save_taught(taught)
    print("Machine Spirit: Auto-promoted to taught knowledge: " + topic)
    return True


# --------------------
# Commands
# --------------------
def cmd_reject(topic):
    notes = get_notes()
    k = find_draft_key_by_topic(notes, topic)
    if not k:
        print("Machine Spirit: No draft found for that topic.")
        return
    del notes["drafts"][k]
    save_notes(notes)
    print("Machine Spirit: Draft rejected (deleted).")


def cmd_set_text(topic, field, text):
    notes = get_notes()
    drafts = notes.get("drafts", {})

    k = find_draft_key_by_topic(notes, topic)
    if not k:
        k = normalize_key(topic)
        drafts[k] = {
            "topic": topic,
            "type": "object",
            "short": "",
            "detailed": "",
            "confidence": 0.2,
            "approvals": 0,
            "created_at": now_iso(),
            "source": "manual_override",
        }

    draft = drafts[k]
    draft[field] = text.strip()
    draft["confidence"] = max(normalize_confidence(draft.get("confidence")), 0.6)
    draft["source"] = "manual_override"
    drafts[k] = draft
    notes["drafts"] = drafts
    save_notes(notes)

    print("Machine Spirit: Saved " + field + " for " + topic + ".")


def cmd_approve(topic):
    notes = get_notes()
    k = find_draft_key_by_topic(notes, topic)

    global LAST_DRAFT_KEY
    if not k:
        if LAST_DRAFT_KEY and LAST_DRAFT_KEY in notes.get("drafts", {}):
            k = LAST_DRAFT_KEY
        else:
            print("Machine Spirit: No draft found to approve.")
            return

    d = notes["drafts"][k]
    conf = normalize_confidence(d.get("confidence"))
    d["confidence"] = max(0.0, min(1.0, conf + CONFIDENCE_APPROVE_GAIN))
    d["approvals"] = int(d.get("approvals", 0)) + 1
    d["approved_at"] = now_iso()
    notes["drafts"][k] = d
    save_notes(notes)

    print("Machine Spirit: Approved and confidence increased.")
    promote_if_ready(d)


def cmd_correct():
    global LAST_DRAFT_KEY
    notes = get_notes()
    if not LAST_DRAFT_KEY or LAST_DRAFT_KEY not in notes.get("drafts", {}):
        print("Machine Spirit: No recent draft to correct.")
        return
    d = notes["drafts"][LAST_DRAFT_KEY]
    conf = normalize_confidence(d.get("confidence"))
    d["confidence"] = max(0.0, min(1.0, conf - CONFIDENCE_CORRECT_PENALTY))
    d["corrected_at"] = now_iso()
    notes["drafts"][LAST_DRAFT_KEY] = d
    save_notes(notes)
    print("Machine Spirit: Correction noted. Confidence reduced.")


def cmd_drafts():
    notes = get_notes()
    drafts = notes.get("drafts", {})
    if not drafts:
        print("Machine Spirit: No drafts saved.")
        return
    print("Machine Spirit: Draft topics:")
    for d in drafts.values():
        print("- " + str(d.get("topic")))


def cmd_draft(topic):
    notes = get_notes()
    k = find_draft_key_by_topic(notes, topic)
    if not k:
        print("Machine Spirit: No draft found for that topic.")
        return
    d = notes["drafts"][k]
    print("Topic: " + str(d.get("topic")))
    print("Type: " + str(d.get("type")))
    print("Confidence: " + str(normalize_confidence(d.get("confidence"))))
    print("Approvals: " + str(int(d.get("approvals", 0))))
    print("")
    print("Short:")
    print(str(d.get("short", "")))
    print("")
    print("Detailed:")
    print(str(d.get("detailed", "")))


def cmd_taught():
    taught = get_taught().get("topics", {})
    if not taught:
        print("Machine Spirit: No taught topics yet.")
        return
    print("Machine Spirit: Taught topics:")
    for t in taught.values():
        print("- " + str(t.get("topic")))


def cmd_unteach(topic):
    taught = get_taught()
    topics = taught.get("topics", {})
    k = normalize_key(topic)
    if k in topics:
        del topics[k]
        taught["topics"] = topics
        save_taught(taught)
        print("Machine Spirit: Removed from taught knowledge: " + topic)
    else:
        print("Machine Spirit: Not found in taught knowledge.")


# --------------------
# Main loop
# --------------------
def main():
    print("Machine Spirit brain online. Type a message, Ctrl+C to exit.")
    print("Safe autonomous: instant research is ON.")
    print("Commands:")
    print("  taught")
    print("  unteach: <topic>")
    print("  drafts")
    print("  draft: <topic>")
    print("  reject: <topic>")
    print("  setshort: <topic> = <text>")
    print("  setdetail: <topic> = <text>")
    print("  approve: <topic>")
    print("  correct:")
    print("")

    while True:
        try:
            msg = input("> ").strip()
        except KeyboardInterrupt:
            print("\nShutting down.")
            break

        if not msg:
            continue

        low = msg.lower().strip()

        if low == "taught":
            cmd_taught()
            continue

        if low.startswith("unteach:"):
            cmd_unteach(msg.split(":", 1)[1].strip())
            continue

        if low == "drafts":
            cmd_drafts()
            continue

        if low.startswith("draft:"):
            cmd_draft(msg.split(":", 1)[1].strip())
            continue

        if low.startswith("reject:"):
            cmd_reject(msg.split(":", 1)[1].strip())
            continue

        if low.startswith("approve:"):
            cmd_approve(msg.split(":", 1)[1].strip())
            continue

        if low.startswith("correct:"):
            cmd_correct()
            continue

        if low.startswith("setshort:"):
            rest = msg.split(":", 1)[1].strip()
            if "=" not in rest:
                print("Machine Spirit: Use setshort: <topic> = <text>")
                continue
            topic, text = rest.split("=", 1)
            cmd_set_text(topic.strip(), "short", text.strip())
            continue

        if low.startswith("setdetail:"):
            rest = msg.split(":", 1)[1].strip()
            if "=" not in rest:
                print("Machine Spirit: Use setdetail: <topic> = <text>")
                continue
            topic, text = rest.split("=", 1)
            cmd_set_text(topic.strip(), "detailed", text.strip())
            continue

        # 1) Answer from taught knowledge first
        if try_taught_answer(msg):
            continue

        # 2) Otherwise, show drafts
        if show_best_for_question(msg):
            continue

        # 3) Otherwise research
        print("Machine Spirit: I do not know this yet. Researching now...")
        queue_research(msg)

        # 4) Try drafts again
        if not show_best_for_question(msg):
            print("Machine Spirit: Research finished but no draft matched yet. Try: drafts")


if __name__ == "__main__":
    main()
