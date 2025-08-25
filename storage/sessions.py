from __future__ import annotations
from typing import Any, Dict, List, Optional
import os

# Reuse the memory layer + lock
from .memory import (  # type: ignore
    _load_mem,
    _atomic_write_mem,
    _FileLock,
    _utc_now,
    LOCK_FILE,
)

def _next_session_id(sessions: List[Dict[str, Any]]) -> int:
    if not sessions:
        return 1
    return max(int(s.get("id", 0)) for s in sessions) + 1


def start_session(title: str = "Conversation") -> Dict[str, Any]:
    with _FileLock(LOCK_FILE):
        mem = _load_mem()
        sessions = mem.get("sessions", [])
        sid = _next_session_id(sessions)
        sess = {
            "id": sid,
            "title": title,
            "started_at": _utc_now(),
            "ended_at": None,
            "messages": [],
        }
        sessions.append(sess)
        mem["sessions"] = sessions
        _atomic_write_mem(mem)
        return sess


def append_message(session_id: int, role: str, text: str) -> None:
    if not text:
        return
    with _FileLock(LOCK_FILE):
        mem = _load_mem()
        sessions = mem.get("sessions", [])
        for s in sessions:
            if int(s.get("id")) == int(session_id):
                s.setdefault("messages", [])
                s["messages"].append({"ts": _utc_now(), "role": role, "text": text})
                _atomic_write_mem(mem)
                return


def end_session(session_id: int) -> None:
    with _FileLock(LOCK_FILE):
        mem = _load_mem()
        sessions = mem.get("sessions", [])
        for s in sessions:
            if int(s.get("id")) == int(session_id):
                if not s.get("ended_at"):
                    s["ended_at"] = _utc_now()
                    _atomic_write_mem(mem)
                return
