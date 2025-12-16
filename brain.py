import json
import re
import subprocess
from pathlib import Path
from datetime import datetime

APP_ROOT = Path(__file__).resolve().parent
RESEARCH_QUEUE_PATH = APP_ROOT / "data" / "research_queue.json"
RESEARCH_NOTES_PATH = APP_ROOT / "data" / "research_notes.json"

LAST_QUESTION = None


# --------------------
# Utils
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


def normalize_key(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"[?!.]+$", "", t)
    return t


# --------------------
# Question → draft keys
# --------------------
def question_to_candidate_keys(question: str):
    q = normalize_key(question)
    keys = [q]

    q = re.sub(r"^(what is|what are|define|explain|tell me about)\s+", "", q).strip()
    keys.append(q)

    q = re.sub(r"^(a|an|the)\s+", "", q).strip()
    keys.append(q)

    parts = q.split()
    if parts:
        keys.append(parts[-1])

    return list(dict.fromkeys([k for k in keys if k]))


# --------------------
# Draft selection
# --------------------
def choose_draft_text(question, draft):
    detailed = draft.get("detailed", "").strip()
    short = draft.get("short", "").strip()
    if detailed and len(detailed) > 60:
        return "detailed draft", detailed
    return "draft", short


def show_best_draft_for_question(question: str) -> bool:
    notes = load_json(RESEARCH_NOTES_PATH, {"drafts": {}})
    drafts = notes.get("drafts", {})

    if not drafts:
        return False

    is_comparison_question = any(
        w in question.lower() for w in [" vs ", "versus", "difference", "compare"]
    )

    # 1) Direct key match
    for key in question_to_candidate_keys(question):
        if key in drafts:
            draft = drafts[key]
            if draft.get("type") == "comparison" and not is_comparison_question:
                continue
            label, text = choose_draft_text(question, draft)
            print(f"Machine Spirit ({label}): {text}")
            print("Machine Spirit: If you like this, approve it with:")
            print(f"  approve: {draft.get('topic')}")
            print("Or view/edit it with:")
            print(f"  draft: {draft.get('topic')}")
            return True

    # 2) Noun-based fallback
    core = question_to_candidate_keys(question)[-1]
    matches = []

    for d in drafts.values():
        if d.get("type") == "comparison" and not is_comparison_question:
            continue
        if core in (d.get("topic") or "").lower():
            matches.append(d)

    if not matches:
        return False

    if len(matches) == 1:
        draft = matches[0]
        label, text = choose_draft_text(question, draft)
        print(f"Machine Spirit ({label}): {text}")
        print("Machine Spirit: If you like this, approve it with:")
        print(f"  approve: {draft.get('topic')}")
        print("Or view/edit it with:")
        print(f"  draft: {draft.get('topic')}")
        return True

    print("Machine Spirit: I found multiple draft topics:")
    for i, d in enumerate(matches, 1):
        print(f"  {i}) {d.get('topic')}")
    print("Use: draft: <topic> or approve: <topic>")
    return True


# --------------------
# Research trigger
# --------------------
def queue_research(topic):
    queue = load_json(RESEARCH_QUEUE_PATH, {"queue": []})
    queue["queue"].append({
        "topic": topic,
        "queued_at": datetime.utcnow().isoformat()
    })
    save_json(RESEARCH_QUEUE_PATH, queue)

    subprocess.run(["python3", "research_worker.py"], stdout=subprocess.DEVNULL)


# --------------------
# Main loop
# --------------------
def main():
    global LAST_QUESTION
    print("Machine Spirit brain online. Type a message, Ctrl+C to exit.")
    print("Safe autonomous: instant research is ON.")

    while True:
        try:
            msg = input("> ").strip()
        except KeyboardInterrupt:
            print("\nShutting down.")
            break

        if not msg:
            continue

        if msg.startswith("approve:"):
            topic = msg.split(":", 1)[1].strip()
            print(f"Machine Spirit: Approved {topic}")
            continue

        if msg.startswith("draft:"):
            topic = msg.split(":", 1)[1].strip()
            notes = load_json(RESEARCH_NOTES_PATH, {"drafts": {}})
            for d in notes.get("drafts", {}).values():
                if d.get("topic") == topic:
                    print(json.dumps(d, indent=2))
                    break
            continue

        LAST_QUESTION = msg

        if show_best_draft_for_question(msg):
            continue

        print("Machine Spirit: I do not have a taught answer for that yet.")
        print("Machine Spirit: Researching now…")
        queue_research(msg)
        if not show_best_draft_for_question(msg):
            print("Machine Spirit: Research complete, but no draft yet. Try again shortly.")


if __name__ == "__main__":
    main()
