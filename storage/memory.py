from __future__ import annotations
import json
import os
import time
import tempfile
import fcntl
from typing import Any, Dict, List, Optional

# Canonical memory location (overridable)
MEMORY_FILE = os.getenv(
    "MEMORY_FILE",
    os.path.expanduser("~/self-learning-ai/memory.json"),
)
LOCK_FILE = MEMORY_FILE + ".lock"


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _default_mem() -> Dict[str, Any]:
    return {
        "notes": [],          # [{ts, text, tags:[]}]
        "knowledge": [],      # [{ts, topic, summary, sources:[], meta:{}}]
        "sessions": [],       # managed via storage.sessions
        "learning_queue": [], # [{ts, topic, status}]
        "errors": [],         # [{ts, context, message, correct_answer, extra}]
        "profile": {},        # user_name, prefs, etc.
    }


def _coerce_mem(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, dict):
        base = _default_mem()
        base.update(obj)
        # ensure all keys exist
        for k in _default_mem().keys():
            base.setdefault(k, _default_mem()[k])
        return base
    if isinstance(obj, list):
        # very old shape: treat as notes
        return {
            "notes": obj,
            "knowledge": [],
            "sessions": [],
            "learning_queue": [],
            "errors": [],
            "profile": {},
        }
    return _default_mem()


def _load_unlocked() -> Dict[str, Any]:
    if not os.path.exists(MEMORY_FILE):
        return _default_mem()
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as fh:
            return _coerce_mem(json.load(fh))
    except Exception:
        return _default_mem()


def _save_unlocked(mem: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(MEMORY_FILE), exist_ok=True)
    data = json.dumps(_coerce_mem(mem), indent=2, ensure_ascii=False)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8",
        dir=os.path.dirname(MEMORY_FILE),
        delete=False,
    ) as tf:
        tf.write(data)
        tmp = tf.name
    os.replace(tmp, MEMORY_FILE)


def _with_lock(fn):
    def wrapper(*args, **kwargs):
        os.makedirs(os.path.dirname(MEMORY_FILE), exist_ok=True)
        with open(LOCK_FILE, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                return fn(*args, **kwargs)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
    return wrapper


# -------- Public API --------

@_with_lock
def load_memory() -> Dict[str, Any]:
    return _load_unlocked()


@_with_lock
def save_memory(mem: Dict[str, Any]) -> None:
    _save_unlocked(mem)


@_with_lock
def append_note(text: str, tags: Optional[List[str]] = None) -> None:
    if not text:
        return
    mem = _load_unlocked()
    mem.setdefault("notes", []).append({
        "ts": utc_now(),
        "text": text,
        "tags": tags or [],
    })
    _save_unlocked(mem)


@_with_lock
def queue_learning(topic: str) -> None:
    topic = (topic or "").strip()
    if not topic:
        return
    mem = _load_unlocked()
    mem.setdefault("learning_queue", []).append({
        "ts": utc_now(),
        "topic": topic,
        "status": "queued",
    })
    _save_unlocked(mem)


@_with_lock
def queue_learning_item(item: Dict[str, Any]) -> None:
    mem = _load_unlocked()
    obj = dict(item)
    obj.setdefault("ts", utc_now())
    obj.setdefault("status", "queued")
    mem.setdefault("learning_queue", []).append(obj)
    _save_unlocked(mem)


@_with_lock
def list_learning_queue() -> List[Dict[str, Any]]:
    mem = _load_unlocked()
    return list(mem.get("learning_queue", []))


@_with_lock
def pop_learning_queue() -> Optional[Dict[str, Any]]:
    mem = _load_unlocked()
    q = mem.get("learning_queue", [])
    for i, item in enumerate(q):
        if item.get("status") == "queued":
            popped = q.pop(i)
            _save_unlocked(mem)
            return popped
    return None


@_with_lock
def add_knowledge(
    topic: str,
    summary: str,
    sources: Optional[List[str]] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    mem = _load_unlocked()
    mem.setdefault("knowledge", []).append({
        "ts": utc_now(),
        "topic": topic,
        "summary": summary,
        "sources": sources or [],
        "meta": meta or {},
    })
    _save_unlocked(mem)


def add_learning_summary(
    topic: str,
    summary: str,
    sources: Optional[List[str]] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    # Back-compat alias
    add_knowledge(topic, summary, sources, meta)
    append_note(f"LEARNED: {topic}", tags=["learn"])


@_with_lock
def log_error(
    context: str,
    message: str,
    correct_answer: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    mem = _load_unlocked()
    mem.setdefault("errors", []).append({
        "ts": utc_now(),
        "context": context,
        "message": message,
        "correct_answer": correct_answer,
        "extra": extra or {},
    })
    _save_unlocked(mem)


def remember_answer(
    question: str,
    answer: str,
    sources: Optional[List[str]] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    """Record a Q/A we had to look up."""
    add_knowledge(question, answer, sources, meta)
