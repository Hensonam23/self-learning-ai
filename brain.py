from __future__ import annotations

import json
import sys
from pathlib import Path

from memory_store import (
    load_base_knowledge,
    save_base_knowledge,
    get_answer,
    teach_answer,
    normalize_key,
)

# --- Make terminal output safe (prevents UnicodeEncodeError) ---
try:
    # Python 3.7+ on many systems
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    # If reconfigure isn't available, we just continue.
    # Worst case: Python will still try to print and might error,
    # but on most setups this fixes it.
    pass

APP_ROOT = Path(__file__).resolve().parent

BASE_KNOWLEDGE_PATH = APP_ROOT / "data" / "knowledge" / "base_knowledge.json"
RESEARCH_QUEUE_PATH = APP_ROOT / "data" / "research_queue.json"
RESEARCH_NOTES_PATH = APP_ROOT / "data" / "research_notes.json"


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def parse_teach_command(text: str) -> tuple[str | None, str | None]:
    raw = text.strip()[len("teach:") :].strip()
    if not raw:
        return None, None

    if " = " in raw:
        q, a = raw.split(" = ", 1)
        return q.strip(), a.strip()
    if "=" in raw:
        q, a = raw.split("=", 1)
        return q.strip(), a.strip()
    if " | " in raw:
        q, a = raw.split(" | ", 1)
        return q.strip(), a.strip()
    if "|" in raw:
        q, a = raw.split("|", 1)
        return q.strip(), a.strip()

    return None, raw.strip()


def is_command(text: str) -> bool:
    t = text.strip().lower()
    if t in {"topics", "drafts"}:
        return True
    if t.startswith(("teach:", "correct:", "find:", "research:", "approve:")):
        return True
    return False


def sanitize_input(text: str) -> str:
    t = (text or "").strip()
    while t.startswith(">"):
        t = t[1:].lstrip()
    return t


def wants_detailed(question: str) -> bool:
    q = (question or "").lower()
    triggers = [
        "explain",
        "detailed",
        "deeper",
        "more detail",
        "more detailed",
        "deep dive",
        "in depth",
    ]
    return any(t in q for t in triggers)


def main() -> None:
    base_store = load_base_knowledge(BASE_KNOWLEDGE_PATH)

    print("Machine Spirit brain online. Type a message, Ctrl+C to exit.")
    print("Commands:")
    print("  teach: ...")
    print("  correct: ...")
    print("  topics")
    print("  find: keyword")
    print("  research: topic")
    print("  drafts")
    print("  approve: topic")

    last_question: str | None = None
    waiting_for_correction = False

    while True:
        try:
            raw_input_text = input("> ")
        except KeyboardInterrupt:
            print("\nShutting down.")
            break

        user_text = sanitize_input(raw_input_text)
        if not user_text:
            continue

        lower = user_text.lower()

        research_queue = load_json(RESEARCH_QUEUE_PATH, {"queue": []})
        research_notes = load_json(RESEARCH_NOTES_PATH, {"drafts": {}})

        # Auto-correction ONLY for normal text, not commands
        if waiting_for_correction and last_question and (not is_command(user_text)):
            teach_answer(base_store, last_question, user_text, source="user_auto_correction")
            save_base_knowledge(BASE_KNOWLEDGE_PATH, base_store)
            print("Machine Spirit: Thank you. I saved your correction.")
            waiting_for_correction = False
            continue

        # topics
        if lower == "topics":
            items = base_store.get("items", {})
            if not items:
                print("Machine Spirit: I have no saved topics yet.")
            else:
                print("Machine Spirit: Saved topics:")
                for k in sorted(items.keys()):
                    q = items[k].get("question", k)
                    print(f" - {q}")
            waiting_for_correction = False
            continue

        # find:
        if lower.startswith("find:"):
            term = user_text[len("find:") :].strip().lower()
            items = base_store.get("items", {})
            if not term:
                print("Machine Spirit: Try: find: osi")
                waiting_for_correction = False
                continue

            matches = []
            for k, v in items.items():
                q = (v.get("question") or k).lower()
                if term in k.lower() or term in q:
                    matches.append(v.get("question", k))

            if not matches:
                print(f"Machine Spirit: No topics matched '{term}'.")
            else:
                print(f"Machine Spirit: Matches for '{term}':")
                for q in sorted(set(matches)):
                    print(f" - {q}")
            waiting_for_correction = False
            continue

        # research:
        if lower.startswith("research:"):
            topic = user_text[len("research:") :].strip()
            if not topic:
                print("Machine Spirit: Try: research: osi model")
                waiting_for_correction = False
                continue

            key = normalize_key(topic)
            q = research_queue.get("queue", [])
            if not isinstance(q, list):
                q = []

            if any((isinstance(item, dict) and item.get("key") == key) for item in q):
                print("Machine Spirit: That topic is already in the research queue.")
                waiting_for_correction = False
                continue

            q.append({"key": key, "topic": topic})
            research_queue["queue"] = q
            save_json(RESEARCH_QUEUE_PATH, research_queue)

            print("Machine Spirit: Added to research queue.")
            waiting_for_correction = False
            continue

        # drafts
        if lower == "drafts":
            drafts = research_notes.get("drafts", {})
            if not drafts:
                print("Machine Spirit: No draft notes yet.")
            else:
                print("Machine Spirit: Draft topics:")
                for k in sorted(drafts.keys()):
                    print(f" - {drafts[k].get('topic', k)}")
            waiting_for_correction = False
            continue

        # approve:
        if lower.startswith("approve:"):
            topic = user_text[len("approve:") :].strip()
            if not topic:
                print("Machine Spirit: Try: approve: osi model")
                waiting_for_correction = False
                continue

            key = normalize_key(topic)
            drafts = research_notes.get("drafts", {})
            draft = drafts.get(key)

            if not draft:
                print("Machine Spirit: I do not have a draft for that topic yet.")
                waiting_for_correction = False
                continue

            answer = (draft.get("detailed") or draft.get("answer") or draft.get("short") or "").strip()
            if not answer:
                print("Machine Spirit: Draft exists, but it has no answer text.")
                waiting_for_correction = False
                continue

            teach_answer(base_store, draft.get("topic", topic), answer, source="research_approved")
            save_base_knowledge(BASE_KNOWLEDGE_PATH, base_store)

            del drafts[key]
            research_notes["drafts"] = drafts
            save_json(RESEARCH_NOTES_PATH, research_notes)

            print("Machine Spirit: Approved. I promoted that draft into taught knowledge.")
            waiting_for_correction = False
            continue

        # teach:
        if lower.startswith("teach:"):
            q, a = parse_teach_command(user_text)
            if q is None and a is not None and last_question:
                q = last_question

            if not q or not a:
                print("Machine Spirit: I need more detail.")
                print("Try: teach: Question = Answer")
                waiting_for_correction = False
                continue

            teach_answer(base_store, q, a, source="user_teach")
            save_base_knowledge(BASE_KNOWLEDGE_PATH, base_store)
            print("Machine Spirit: Learned. I saved that.")
            waiting_for_correction = False
            continue

        # correct:
        if lower.startswith("correct:"):
            correction = user_text[len("correct:") :].strip()
            if not last_question or not correction:
                print("Machine Spirit: I need a last question to attach that correction to.")
                waiting_for_correction = False
                continue

            teach_answer(base_store, last_question, correction, source="user_correction")
            save_base_knowledge(BASE_KNOWLEDGE_PATH, base_store)
            print("Machine Spirit: Got it. I updated my answer for that topic.")
            waiting_for_correction = False
            continue

        # Normal Q&A
        question = user_text
        last_question = question

        known = get_answer(base_store, question)
        if known:
            print(f"Machine Spirit: {known}")
            waiting_for_correction = False
            continue

        # Draft lookup (short vs detailed)
        key = normalize_key(question)
        draft = research_notes.get("drafts", {}).get(key)

        if draft:
            if wants_detailed(question):
                text = (draft.get("detailed") or draft.get("answer") or draft.get("short") or "").strip()
                label = "detailed draft"
            else:
                text = (draft.get("short") or draft.get("answer") or draft.get("detailed") or "").strip()
                label = "draft"

            if text:
                print(f"Machine Spirit ({label}): {text}")
                print("Machine Spirit: If you like this, approve it with:")
                print(f"  approve: {draft.get('topic', question)}")
                waiting_for_correction = False
                continue

        print("Machine Spirit: I do not have a taught answer for that yet.")
        print("You can:")
        print(" - reply with your correction and I will learn it")
        print(" - use: teach: <question> = <answer>")
        print(" - use: research: <topic> to queue it for auto research")
        waiting_for_correction = True


if __name__ == "__main__":
    main()
