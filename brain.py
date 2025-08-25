#!/usr/bin/env python3
from __future__ import annotations
import os, json, time, tempfile

# Use your existing lightweight on-device responder
# (web scraping + heuristics), no cloud models.
try:
    from answer_engine import respond as local_respond  # type: ignore
except Exception:
    def local_respond(text: str) -> str:
        return "I’m online but the local answer engine isn’t loaded."

def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def _ensure_dir(p: str) -> None:
    d = os.path.dirname(p)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)

def _coerce_msgs(obj) -> list:
    # Accept [], {"messages":[...]}, or any accidental shape
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        m = obj.get("messages")
        if isinstance(m, list):
            return m
    return []

def _load_list(path: str) -> list:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return _coerce_msgs(json.load(fh))
    except Exception:
        return []

def _save_list(path: str, items: list) -> None:
    _ensure_dir(path)
    data = json.dumps(items[-1000:], ensure_ascii=False, indent=2)
    # atomic write
    with tempfile.NamedTemporaryFile("w", encoding="utf-8",
                                     dir=os.path.dirname(path), delete=False) as tf:
        tf.write(data)
        tmp = tf.name
    os.replace(tmp, path)

class Brain:
    """
    Local-only brain that calls the repo's answer_engine.respond()
    and persists a rolling chat log to mem_path.

    Chat log schema: [{ts, role, text}]
    """
    def __init__(self, mem_path: str | None = None):
        default_path = os.path.expanduser("~/self-learning-ai/data/chat_default.json")
        self.mem_path = os.path.expanduser(mem_path or default_path)
        self._msgs = _load_list(self.mem_path)

    def _append(self, role: str, text: str) -> None:
        if not text:
            return
        self._msgs.append({"ts": _utc_now(), "role": role, "text": text})
        _save_list(self.mem_path, self._msgs)

    def answer(self, text: str) -> str:
        user = (text or "").strip()
        if not user:
            return "Say something I can help with."
        self._append("user", user)
        try:
            reply = local_respond(user)
        except Exception as e:
            reply = f"error: {e}"
        self._append("assistant", reply)
        return reply
