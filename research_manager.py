#!/usr/bin/env python3

"""
Research manager for the Machine Spirit.

This does NOT perform live web requests itself.
It keeps a structured queue of topics or URLs that the Machine Spirit
has flagged as needing deeper research.

A separate script (research_worker.py) can process this queue and
attach real research results.
"""

import json
import os
import time
from typing import Any, Dict, List


RESEARCH_QUEUE_PATH = "data/research_queue.json"


def _ensure_dir(path: str) -> None:
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)


def _load_queue(path: str) -> List[Dict[str, Any]]:
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


def _save_queue(path: str, data: List[Dict[str, Any]]) -> None:
    _ensure_dir(path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


class ResearchManager:
    """
    Provides simple methods to queue research tasks and to inspect/update
    the queue.

    Types of tasks:
    - "topic": a user question or topic that needs better answers.
    - "url": a URL from a 'scan <url>' command.

    Each entry is marked pending and can be processed later by a
    separate research script.
    """

    def __init__(self, path: str = RESEARCH_QUEUE_PATH) -> None:
        self.path = path

    # ---- low-level helpers -------------------------------------------------

    def _load(self) -> List[Dict[str, Any]]:
        return _load_queue(self.path)

    def _save(self, queue: List[Dict[str, Any]]) -> None:
        _save_queue(self.path, queue)

    # ---- public API: add items --------------------------------------------

    def queue_topic(
        self,
        user_text: str,
        reason: str = "needs_research",
        channel: str = "cli",
    ) -> None:
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        queue = self._load()
        entry: Dict[str, Any] = {
            "timestamp": ts,
            "type": "topic",
            "channel": channel,
            "user_text": user_text,
            "reason": reason,
            "status": "pending",
            "notes_key": None,  # key into research_notes.json if we attach notes
        }
        queue.append(entry)
        self._save(queue)

    def queue_url(
        self,
        url: str,
        reason: str = "scan_command",
        channel: str = "cli",
    ) -> None:
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        queue = self._load()
        entry: Dict[str, Any] = {
            "timestamp": ts,
            "type": "url",
            "channel": channel,
            "url": url,
            "reason": reason,
            "status": "pending",
            "notes_key": None,
        }
        queue.append(entry)
        self._save(queue)

    # ---- public API: inspect / update queue --------------------------------

    def get_queue(self) -> List[Dict[str, Any]]:
        """
        Return the full queue list.
        """
        return self._load()

    def save_queue(self, queue: List[Dict[str, Any]]) -> None:
        """
        Overwrite the queue with a modified list.
        """
        self._save(queue)

    def list_pending_indices(self) -> List[int]:
        """
        Convenience: returns indices of entries whose status is 'pending'.
        """
        queue = self._load()
        return [i for i, e in enumerate(queue) if e.get("status") == "pending"]
