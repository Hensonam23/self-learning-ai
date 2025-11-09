from __future__ import annotations
from typing import Any, Dict, List

from .memory import load_memory, save_memory, utc_now


def _next_session_id(sessions: List[Dict[str, Any]]) -> int:
    if not sessions:
        return 1
    return max(int(s.get("id", 0) or 0) for s in sessions) + 1


def start_session(title: str = "Conversation") -> Dict[str, Any]:
    mem = load_memory()
    sessions: List[Dict[str, Any]] = mem.get("sessions", [])
    sid = _next_session_id(sessions)
    sess = {
        "id": sid,
        "title": title,
        "started_at": utc_now(),
        "ended_at": None,
        "messages": [],
    }
    sessions.append(sess)
    mem["sessions"] = sessions
    save_memory(mem)
    return sess


def append_message(session_id: int, role: str, text: str) -> None:
    text = (text or "").strip()
    if not text:
        return
    mem = load_memory()
    sessions: List[Dict[str, Any]] = mem.get("sessions", [])
    for s in sessions:
        if int(s.get("id", 0)) == int(session_id):
            msgs = s.setdefault("messages", [])
            msgs.append({"ts": utc_now(), "role": role, "text": text})
            mem["sessions"] = sessions
            save_memory(mem)
            return


def end_session(session_id: int) -> None:
    mem = load_memory()
    sessions: List[Dict[str, Any]] = mem.get("sessions", [])
    for s in sessions:
        if int(s.get("id", 0)) == int(session_id):
            if not s.get("ended_at"):
                s["ended_at"] = utc_now()
                mem["sessions"] = sessions
                save_memory(mem)
            return
