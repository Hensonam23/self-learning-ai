from __future__ import annotations

from pathlib import Path

from memory_store import load_base_knowledge, save_base_knowledge, get_answer, teach_answer

APP_ROOT = Path(__file__).resolve().parent
BASE_KNOWLEDGE_PATH = APP_ROOT / "data" / "knowledge" / "base_knowledge.json"


def parse_teach_command(text: str) -> tuple[str | None, str | None]:
    """
    Returns (question, answer)
    Supported:
      teach: Question = Answer
      teach: Question | Answer
      teach: Answer   (question will be filled by caller using last_question)
    """
    raw = text.strip()[len("teach:") :].strip()
    if not raw:
        return None, None

    # Try separators first
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

    # No separator -> caller can use last_question as question
    return None, raw.strip()


def main() -> None:
    base_store = load_base_knowledge(BASE_KNOWLEDGE_PATH)

    print("Machine Spirit brain online. Type a message, Ctrl+C to exit.")
    print("Commands: teach: ...   correct: ...")
    print("Examples:")
    print("  teach: OSI model = 7 layers... ")
    print("  teach: OSI model | 7 layers... ")
    print("  teach: 7 layers...   (teaches the last question)")
    print("  correct: ...         (corrects the last question)")

    last_question: str | None = None
    last_answer: str | None = None
    waiting_for_correction = False

    while True:
        try:
            user_text = input("> ").strip()
        except KeyboardInterrupt:
            print("\nShutting down.")
            break

        if not user_text:
            continue

        lower = user_text.lower()

        # --- teach: command ---
        if lower.startswith("teach:"):
            q, a = parse_teach_command(user_text)
            if q is None and a is not None and last_question:
                q = last_question

            if not q or not a:
                print("Machine Spirit: I need more detail.")
                print("Try: teach: Question = Answer")
                continue

            teach_answer(base_store, q, a, source="user_teach")
            save_base_knowledge(BASE_KNOWLEDGE_PATH, base_store)
            print("Machine Spirit: Learned. I saved that.")
            waiting_for_correction = False
            continue

        # --- correct: command ---
        if lower.startswith("correct:"):
            correction = user_text[len("correct:") :].strip()
            if not last_question or not correction:
                print("Machine Spirit: I need a last question to attach that correction to.")
                continue

            teach_answer(base_store, last_question, correction, source="user_correction")
            save_base_knowledge(BASE_KNOWLEDGE_PATH, base_store)
            print("Machine Spirit: Got it. I updated my answer for that topic.")
            waiting_for_correction = False
            continue

        # --- automatic correction learning ---
        # If the brain just admitted it didn't know, treat your next message as the correction.
        if waiting_for_correction and last_question:
            teach_answer(base_store, last_question, user_text, source="user_auto_correction")
            save_base_knowledge(BASE_KNOWLEDGE_PATH, base_store)
            print("Machine Spirit: Thank you. I saved your correction.")
            waiting_for_correction = False
            continue

        # --- normal Q&A flow ---
        question = user_text
        last_question = question

        known = get_answer(base_store, question)
        if known:
            last_answer = known
            print(f"Machine Spirit: {known}")
            waiting_for_correction = False
        else:
            # Keep your original vibe, but now it has a clear “teach me” mode.
            last_answer = None
            print("Machine Spirit: I do not have a taught answer for that yet.")
            print("If you want, reply with your correction and I will learn it.")
            print("Or use: teach: <question> = <answer>")
            waiting_for_correction = True


if __name__ == "__main__":
    main()
