from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from memory_store import (
    load_base_knowledge,
    save_base_knowledge,
    get_answer,
    teach_answer,
    normalize_key,
)

# Safe printing (prevents UnicodeEncodeError)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
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


def parse_set_command(text: str, prefix: str) -> tuple[str | None, str | None]:
    raw = text.strip()[len(prefix) :].strip()
    if not raw:
        return None, None
    if " = " in raw:
        a, b = raw.split(" = ", 1)
        return a.strip(), b.strip()
    if "=" in raw:
        a, b = raw.split("=", 1)
        return a.strip(), b.strip()
    return None, raw.strip()


def sanitize_input(text: str) -> str:
    t = (text or "").strip()
    while t.startswith(">"):
        t = t[1:].lstrip()
    return t


def is_command(text: str) -> bool:
    t = text.strip().lower()
    if t in {"topics", "drafts"}:
        return True
    if t.startswith(
        (
            "teach:",
            "correct:",
            "find:",
            "research:",
            "approve:",
            "draft:",
            "reject:",
            "setshort:",
            "setdetail:",
        )
    ):
        return True
    return False


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


def question_to_candidate_keys(question: str) -> list[str]:
    """
    Turns things like:
      "what is dhcp"
      "explain dhcp"
      "dhcp in more detail"
    into candidate draft keys so drafts match easier.
    """
    q = (question or "").strip().lower()
    q = q.replace("—", " ").replace("–", " ")
    q = re.sub(r"[?!.]+$", "", q).strip()

    candidates = []

    # base key
    candidates.append(normalize_key(q))

    # remove common leading phrases
    lead_patterns = [
        r"^what is\s+",
        r"^what are\s+",
        r"^define\s+",
        r"^explain\s+",
        r"^tell me about\s+",
        r"^give me\s+",
        r"^help me understand\s+",
    ]
    q2 = q
    for pat in lead_patterns:
        q2 = re.sub(pat, "", q2).strip()

    # remove common trailing phrases
    tail_patterns = [
        r"\s+in more detail$",
        r"\s+more detail$",
        r"\s+in detail$",
        r"\s+more detailed$",
    ]
    for pat in tail_patterns:
        q2 = re.sub(pat, "", q2).strip()

    if q2 and q2 != q:
        candidates.append(normalize_key(q2))

    # also try last token if short (helps "what is dhcp")
    parts = q2.split()
    if len(parts) >= 2:
        last = parts[-1].strip()
        if last:
            candidates.append(normalize_key(last))

    # de-dupe while preserving order
    out = []
    for c in candidates:
        c = (c or "").strip().lower()
        if c and c not in out:
            out.append(c)
    return out


def main() -> None:
    base_store = load_base_knowledge(BASE_KNOWLEDGE_PATH)

    print("Machine Spirit brain online. Type a message, Ctrl+C to exit.")
    print("Commands:")
    print("  teach: <q> = <a>")
    print("  correct: <a>  (applies to last question)")
    print("  topics")
    print("  find: keyword   (searches taught + drafts)")
    print("  research: topic")
    print("  drafts")
    print("  draft: topic    (shows short + detailed)")
    print("  reject: topic   (deletes a draft)")
    print("  setshort: topic = text   (or setshort: text after viewing a draft)")
    print("  setdetail: topic = text  (or setdetail: text after viewing a draft)")
    print("  approve: topic")

    last_question: str | None = None
    waiting_for_correction = False
    last_draft_key: str | None = None

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

        # Auto-correction only for normal text, not commands
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

        # find: taught + drafts
        if lower.startswith("find:"):
            term = user_text[len("find:") :].strip().lower()
            if not term:
                print("Machine Spirit: Try: find: osi")
                waiting_for_correction = False
                continue

            taught = base_store.get("items", {})
            taught_matches = []
            for k, v in taught.items():
                q = (v.get("question") or k).lower()
                if term in k.lower() or term in q:
                    taught_matches.append(v.get("question", k))

            drafts = research_notes.get("drafts", {})
            draft_matches = []
            for k, d in drafts.items():
                topic = (d.get("topic") or k).lower()
                keywords = d.get("keywords") or []
                kw_text = " ".join([str(x).lower() for x in keywords])
                if term in k.lower() or term in topic or term in kw_text:
                    draft_matches.append(d.get("topic", k))

            if not taught_matches and not draft_matches:
                print(f"Machine Spirit: No matches for '{term}'.")
            else:
                if taught_matches:
                    print("Machine Spirit: Taught matches:")
                    for q in sorted(set(taught_matches)):
                        print(f" - {q}")
                if draft_matches:
                    print("Machine Spirit: Draft matches:")
                    for q in sorted(set(draft_matches)):
                        print(f" - {q}")

            waiting_for_correction = False
            continue

        # research:
        if lower.startswith("research:"):
            topic = user_text[len("research:") :].strip()
            if not topic:
                print("Machine Spirit: Try: research: dhcp")
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

        # drafts list
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

        # draft: show both
        if lower.startswith("draft:"):
            topic = user_text[len("draft:") :].strip()
            if not topic:
                print("Machine Spirit: Try: draft: dhcp")
                waiting_for_correction = False
                continue

            key = normalize_key(topic)
            draft = research_notes.get("drafts", {}).get(key)
            if not draft:
                print("Machine Spirit: No draft found for that topic.")
                waiting_for_correction = False
                continue

            last_draft_key = key
            short = (draft.get("short") or "").strip()
            detailed = (draft.get("detailed") or draft.get("answer") or "").strip()

            print("Machine Spirit (draft short):")
            print(short if short else "(empty)")

            print("\nMachine Spirit (draft detailed):")
            print(detailed if detailed else "(empty)")

            print("\nMachine Spirit: You can edit it with:")
            print("  setshort: <text>")
            print("  setdetail: <text>")
            print("Or approve it with:")
            print(f"  approve: {draft.get('topic', topic)}")

            waiting_for_correction = False
            continue

        # reject:
        if lower.startswith("reject:"):
            topic = user_text[len("reject:") :].strip()
            if not topic:
                print("Machine Spirit: Try: reject: dhcp")
                waiting_for_correction = False
                continue

            key = normalize_key(topic)
            drafts = research_notes.get("drafts", {})
            if key not in drafts:
                print("Machine Spirit: No draft found for that topic.")
                waiting_for_correction = False
                continue

            del drafts[key]
            research_notes["drafts"] = drafts
            save_json(RESEARCH_NOTES_PATH, research_notes)

            if last_draft_key == key:
                last_draft_key = None

            print("Machine Spirit: Draft rejected (deleted).")
            waiting_for_correction = False
            continue

        # setshort:
        if lower.startswith("setshort:"):
            topic, text = parse_set_command(user_text, "setshort:")
            if not text:
                print("Machine Spirit: Try: setshort: dhcp = short text")
                waiting_for_correction = False
                continue

            if topic:
                key = normalize_key(topic)
            else:
                if not last_draft_key:
                    print("Machine Spirit: No last draft selected. Use: draft: <topic> first.")
                    waiting_for_correction = False
                    continue
                key = last_draft_key

            drafts = research_notes.get("drafts", {})
            if key not in drafts:
                print("Machine Spirit: No draft found for that topic.")
                waiting_for_correction = False
                continue

            drafts[key]["short"] = text.strip()
            research_notes["drafts"] = drafts
            save_json(RESEARCH_NOTES_PATH, research_notes)

            last_draft_key = key
            print("Machine Spirit: Updated draft short.")
            waiting_for_correction = False
            continue

        # setdetail:
        if lower.startswith("setdetail:"):
            topic, text = parse_set_command(user_text, "setdetail:")
            if not text:
                print("Machine Spirit: Try: setdetail: dhcp = detailed text")
                waiting_for_correction = False
                continue

            if topic:
                key = normalize_key(topic)
            else:
                if not last_draft_key:
                    print("Machine Spirit: No last draft selected. Use: draft: <topic> first.")
                    waiting_for_correction = False
                    continue
                key = last_draft_key

            drafts = research_notes.get("drafts", {})
            if key not in drafts:
                print("Machine Spirit: No draft found for that topic.")
                waiting_for_correction = False
                continue

            drafts[key]["detailed"] = text.strip()
            research_notes["drafts"] = drafts
            save_json(RESEARCH_NOTES_PATH, research_notes)

            last_draft_key = key
            print("Machine Spirit: Updated draft detailed.")
            waiting_for_correction = False
            continue

        # approve:
        if lower.startswith("approve:"):
            topic = user_text[len("approve:") :].strip()
            if not topic:
                print("Machine Spirit: Try: approve: dhcp")
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

            if last_draft_key == key:
                last_draft_key = None

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

        # Normal Q and A
        question = user_text
        last_question = question

        known = get_answer(base_store, question)
        if known:
            print(f"Machine Spirit: {known}")
            waiting_for_correction = False
            continue

        # Draft lookup with candidate keys
        drafts = research_notes.get("drafts", {})
        candidate_keys = question_to_candidate_keys(question)
        draft = None
        used_key = None
        for ck in candidate_keys:
            if ck in drafts:
                draft = drafts[ck]
                used_key = ck
                break

        if draft and used_key:
            last_draft_key = used_key
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
                print("Or view/edit it with:")
                print(f"  draft: {draft.get('topic', question)}")
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
