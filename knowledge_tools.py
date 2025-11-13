#!/usr/bin/env python3

"""
Knowledge tools for the Machine Spirit.

Recognizes simple command-style inputs and handles them separately from
normal Q&A, while still returning a text answer that goes through the
style layer.

Supported patterns:

- "scan <url>"
- "summarize <something>"
- "explain <something> like I'm new"
"""

import json
import os
import time
from typing import Any, Dict, Optional


TOOLS_LOG_PATH = "data/knowledge_tools_log.json"


def _ensure_dir(path: str) -> None:
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)


def _load_log(path: str):
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


def _save_log(path: str, data) -> None:
    _ensure_dir(path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


class KnowledgeTools:
    """
    Simple pattern-based tool layer.

    This does NOT fetch the web itself. It just gives structure and
    logging for commands so the behavior is separated from normal chat.
    """

    # ---- utility ----------------------------------------------------------

    def _strip_leading_markers(self, text: str) -> str:
        """
        Strip leading '>' characters and spaces so that inputs like:

            "> scan https://example.com"
            ">> summarize this..."

        are treated as:

            "scan https://example.com"
            "summarize this..."
        """
        t = text.strip()
        while t.startswith(">"):
            t = t[1:].lstrip()
        return t

    # ---- public API -------------------------------------------------------

    def handle(self, user_text: str) -> Optional[Dict[str, Any]]:
        """
        If this looks like a knowledge-tool command, return a dict:

            {
              "tool": "scan" | "summarize" | "explain_new",
              "answer": "<text to send back>",
              "meta": {...}
            }

        Otherwise return None and the normal Brain pipeline will run.
        """
        raw = user_text or ""
        text = self._strip_leading_markers(raw)
        if not text:
            return None

        lower = text.lower()

        # Tool 1: scan <url>
        if lower.startswith("scan "):
            url = text[5:].strip()
            if not url:
                return {
                    "tool": "scan",
                    "answer": (
                        "You asked me to scan a URL, but no address was provided. "
                        "Say 'scan <url>' with a full address."
                    ),
                    "meta": {"ok": False},
                }
            return self._handle_scan(url)

        # Tool 2: summarize <something>
        if lower.startswith("summarize "):
            target = text[len("summarize "):].strip()
            if not target:
                return {
                    "tool": "summarize",
                    "answer": (
                        "You asked me to summarize something, but did not say what. "
                        "Use 'summarize <topic or text>'."
                    ),
                    "meta": {"ok": False},
                }
            return self._handle_summarize(target)

        # Tool 3: explain <something> like I'm new
        if "like i'm new" in lower or "like im new" in lower:
            return self._handle_explain_new(text, lower)

        return None

    # ---- tool implementations ---------------------------------------------

    def _handle_scan(self, url: str) -> Dict[str, Any]:
        """
        For now we just log the URL for future analysis. The idea is that
        a later research module (or you via web mode) can pull it.
        """
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        log = _load_log(TOOLS_LOG_PATH)

        entry = {
            "timestamp": ts,
            "tool": "scan",
            "url": url,
        }
        log.append(entry)
        _save_log(TOOLS_LOG_PATH, log)

        ans = (
            "I have recorded this URL for analysis: {url}\n\n"
            "Later, you can say things like 'summarize that page' or "
            "'explain that page like I'm new', and I will treat it as a "
            "stored reference point."
        ).format(url=url)

        return {
            "tool": "scan",
            "answer": ans,
            "meta": {"ok": True, "url": url},
        }

    def _handle_summarize(self, target: str) -> Dict[str, Any]:
        """
        Very lightweight summarization placeholder.

        We do NOT actually have deep NLP here, but giving you a structured
        response still makes this more useful than generic Q&A.
        """
        ans = (
            "Here is a brief summary based only on the text you gave me:\n\n"
            f"{target}\n\n"
            "This is a surface-level summary. If it seems off, clarify or "
            "correct me and I will update my understanding."
        )

        return {
            "tool": "summarize",
            "answer": ans,
            "meta": {"ok": True, "target": target},
        }

    def _handle_explain_new(self, text: str, lower: str) -> Dict[str, Any]:
        """
        Extract the topic from patterns like:
          'explain docker like I'm new'
          'explain what ram is like im new'
        """
        core = text
        lower_core = lower

        # Strip leading "explain "
        if lower_core.startswith("explain "):
            core = text[len("explain "):].strip()
            lower_core = lower[len("explain "):].strip()

        # Remove trailing "like i'm new" or "like im new"
        for mark in ("like i'm new", "like im new"):
            idx = lower_core.find(mark)
            if idx != -1:
                core = core[:idx].strip()
                break

        topic = core.strip()
        if not topic:
            topic = "this"

        ans = (
            "Let me explain {topic} in simple terms.\n\n"
            "Think of it this way: {topic} is something you can understand by "
            "breaking it into smaller parts and focusing on the basics first. "
            "If my explanation feels off or confusing, tell me what part is wrong "
            "and I will refine how I explain it next time."
        ).format(topic=topic)

        return {
            "tool": "explain_new",
            "answer": ans,
            "meta": {"ok": True, "topic": topic},
        }
