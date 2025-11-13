#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional

try:
    # Your existing local answer engine
    from answer_engine import respond as local_respond  # type: ignore
except Exception:
    def local_respond(text: str) -> str:
        return "I'm online but my local answer engine is not loaded."

from teachability_manager import TeachabilityManager


CHATLOG_PATH = "data/chatlog.json"


# ---------- helpers ----------

def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _ensure_dir(path: str) -> None:
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)


def _load(path: str) -> List[Any]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, list) else []
    except Exception:
        backup = f"{path}.corrupt_{int(time.time())}"
        try:
            os.replace(path, backup)
        except Exception:
            pass
        return []


def _save(path: str, data: List[Any]) -> None:
    _ensure_dir(path)
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".tmp", suffix=".json", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def _append_chatlog(entry: Dict[str, Any]) -> None:
    log = _load(CHATLOG_PATH)
    log.append(entry)
    _save(CHATLOG_PATH, log)


def _safe_print(text: str) -> None:
    """
    Print text without crashing on encoding errors.
    """
    try:
        print(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))
        sys.stdout.buffer.write(b"\n")
        sys.stdout.flush()


# ---------- Brain ----------

class Brain:
    def __init__(self) -> None:
        self.teach = TeachabilityManager()
        # This is the last REAL question we answered (not corrections)
        self.last_question_for_teach: Optional[str] = None
        self.last_answer_for_teach: Optional[str] = None

    def handle_message(self, user_text: str, channel: str = "cli") -> Dict[str, Any]:
        ts = _utc_now()

        # 1) See if this message is correcting the last REAL question
        teaching_entry = self.teach.record_correction(
            previous_question=self.last_question_for_teach,
            previous_answer=self.last_answer_for_teach,
            user_message=user_text,
        )

        # 2) Look for any teaching that matches THIS message as a question
        taught = self.teach.lookup(user_text)
        used_teaching = taught is not None

        if taught is not None:
            # Inject canonical explanation into the prompt
            prompt = (
                "You were corrected by the user previously on this topic.\n"
                "They gave you this explanation, which is the source of truth:\n\n"
                f"{taught['canonical_explanation']}\n\n"
                "Now answer the user's new question in your own words:\n\n"
                f"User: {user_text}"
            )
        else:
            prompt = user_text

        # 3) Call local answer engine
        try:
            answer_text = local_respond(prompt)
        except Exception as e:
            answer_text = f"Error while calling local answer engine: {e!r}"

        # 4) Update last REAL question only if this message was NOT a correction
        if teaching_entry is None:
            self.last_question_for_teach = user_text
            self.last_answer_for_teach = answer_text
        # If teaching_entry is not None, we keep last_question_for_teach pointing
        # to the original question that was corrected.

        # 5) Log
        entry: Dict[str, Any] = {
            "timestamp": ts,
            "channel": channel,
            "question": user_text,
            "answer": answer_text,
            "used_teaching": used_teaching,
            "teaching_question": taught["question"] if taught else None,
            "teaching_entry_created_or_updated": bool(teaching_entry),
        }
        _append_chatlog(entry)

        return entry


brain = Brain()


def handle_message(user_text: str, channel: str = "cli") -> Dict[str, Any]:
    return brain.handle_message(user_text, channel=channel)


def main() -> None:
    _safe_print("Machine Spirit brain online. Type a message, Ctrl+C to exit.")
    while True:
        try:
            user_text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            _safe_print("\nShutting down.")
            break

        if not user_text:
            continue

        entry = handle_message(user_text, channel="cli")
        _safe_print(entry["answer"])


if __name__ == "__main__":
    main()
