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
from style_manager import StyleManager
from insight_manager import InsightManager
from knowledge_tools import KnowledgeTools
from research_manager import ResearchManager


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
        self.style = StyleManager()
        self.insight = InsightManager()
        self.tools = KnowledgeTools()
        self.research = ResearchManager()
        # Last REAL question we answered (not corrections)
        self.last_question_for_teach: Optional[str] = None
        self.last_answer_for_teach: Optional[str] = None

    def handle_message(self, user_text: str, channel: str = "cli") -> Dict[str, Any]:
        ts = _utc_now()

        # 0) Try knowledge-tools first (scan/summarize/explain-like-new)
        tool_result = self.tools.handle(user_text)
        if tool_result is not None:
            raw_answer = tool_result["answer"]
            tool_name = tool_result.get("tool")
            meta = tool_result.get("meta", {})

            # If tool suggests research (for example, scan <url>), queue it
            qr = meta.get("queue_research")
            if isinstance(qr, dict):
                if qr.get("type") == "url" and "url" in qr:
                    self.research.queue_url(
                        url=qr["url"],
                        reason="scan_command",
                        channel=channel,
                    )

            # For tools, we treat confidence as medium and skip teachability
            style_context: Dict[str, Any] = {
                "used_teaching": False,
                "channel": channel,
                "confidence": "medium",
                "needs_teaching": False,
                "needs_research": False,
                "tool": tool_name,
            }
            answer_text = self.style.format_answer(
                user_text=user_text,
                raw_answer=raw_answer,
                context=style_context,
            )

            # Still update last question so you can correct tool outputs
            self.last_question_for_teach = user_text
            self.last_answer_for_teach = answer_text

            entry: Dict[str, Any] = {
                "timestamp": ts,
                "channel": channel,
                "question": user_text,
                "answer": answer_text,
                "used_teaching": False,
                "confidence": "medium",
                "needs_teaching": False,
                "needs_research": False,
                "tool_used": tool_name,
                "teaching_question": None,
                "teaching_entry_created_or_updated": False,
            }
            _append_chatlog(entry)
            return entry

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

        # 3) Call local answer engine to get a raw answer
        try:
            raw_answer = local_respond(prompt)
        except Exception as e:
            raw_answer = f"Error while calling local answer engine: {e!r}"

        # 4) Run the insight layer to tag confidence
        insight_context: Dict[str, Any] = {
            "used_teaching": used_teaching,
            "channel": channel,
        }
        analysis = self.insight.analyze(
            user_text=user_text,
            raw_answer=raw_answer,
            context=insight_context,
        )
        confidence = analysis["confidence"]
        needs_teaching = analysis["needs_teaching"]
        needs_research = analysis["needs_research"]

        # 4.5) If this clearly needs research, queue the topic
        if needs_research:
            self.research.queue_topic(
                user_text=user_text,
                reason="insight_flag_needs_research",
                channel=channel,
            )

        # 5) Run the style / persona layer as a final pass
        style_context: Dict[str, Any] = {
            "used_teaching": used_teaching,
            "channel": channel,
            "confidence": confidence,
            "needs_teaching": needs_teaching,
            "needs_research": needs_research,
        }
        answer_text = self.style.format_answer(
            user_text=user_text,
            raw_answer=raw_answer,
            context=style_context,
        )

        # 6) Update last REAL question only if this message was NOT a correction
        if teaching_entry is None:
            self.last_question_for_teach = user_text
            self.last_answer_for_teach = answer_text

        # 7) Log
        entry: Dict[str, Any] = {
            "timestamp": ts,
            "channel": channel,
            "question": user_text,
            "answer": answer_text,
            "used_teaching": used_teaching,
            "confidence": confidence,
            "needs_teaching": needs_teaching,
            "needs_research": needs_research,
            "tool_used": None,
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
