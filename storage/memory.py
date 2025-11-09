# storage/memory.py
from __future__ import annotations
import json, os, time, tempfile, fcntl
from typing import Any, Dict, List, Optional

# Where to store memory (overridable via env)
MEMORY_FILE = os.getenv("MEMORY_FILE", os.path.expanduser("~/self-learning-ai/memory.json"))
LOCK_FILE   = MEMORY_FILE + ".lock"

def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def _default_mem() -> Dict[str, Any]:
    return {
        "notes": [],            # [{ts, text, tags:[]}]
        "knowledge": [],        # [{ts, topic, summary, sources:[], meta:{}}]
        "sessions": [],         # managed by storage.sessions
        "learning_queue": [],   # [{ts, topic, status}]
        "errors": [],           # [{ts, context, message, correct_answer, extra}]
    }

def _coerce_mem(obj: Any) -> Dict[str, Any]:
    # Migrate older shapes if needed
    if isinstance(obj, list):
        return {
            "notes": obj,
            "knowledge": [],
            "sessions": [],
            "learning_queue": [],
            "errors": [],
        }
    if isinstance(obj, dict):
        obj.setdefault("notes", [])
        obj.setdefault("knowledge", [])
        obj.setdefault("sessions", [])
        obj.setdefault("learning_queue", [])
        obj.setdefault("errors", [])
        return obj
    return _default_mem()

def _load_unlocked() -> Dict[str, Any]:
    if not os.path.exists(MEMORY_FILE):
        return _default_mem()
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as fh:
            return _coerce_mem(json.load(fh))
    except Exception:
        # Corrupt or empty file: fall back
        return _default_mem()

def _save_unlocked(mem: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(MEMORY_FILE), exist_ok=True)
    data = json.dumps(mem, indent=2, ensure_ascii=False)
    # Atomic write
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=os.path.dirname(MEMORY_FILE), delete=False) as tf:
        tf.write(data)
        tmpname = tf.name
    os.replace(tmpname, MEMORY_FILE)

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

# ---------------- Public API ----------------

@_with_lock
def load_memory() -> Dict[str, Any]:
    """Load the entire memory dictionary (locked)."""
    return _load_unlocked()

@_with_lock
def save_memory(mem: Dict[str, Any]) -> None:
    """Persist the entire memory dictionary (locked)."""
    _save_unlocked(_coerce_mem(mem))

@_with_lock
def append_note(text: str, tags: Optional[List[str]] = None) -> None:
    mem = _load_unlocked()
    mem["notes"].append({
        "ts": _utc_now(),
        "text": text,
        "tags": tags or []
    })
    _save_unlocked(mem)

@_with_lock
def queue_learning(topic: str) -> None:
    mem = _load_unlocked()
    mem["learning_queue"].append({
        "ts": _utc_now(),
        "topic": topic,
        "status": "queued"
    })
    _save_unlocked(mem)

@_with_lock
def queue_learning_item(item: Dict[str, Any]) -> None:
    """Append a fully specified learning queue item."""
    mem = _load_unlocked()
    obj = dict(item)
    obj.setdefault("ts", _utc_now())
    obj.setdefault("status", "queued")
    mem["learning_queue"].append(obj)
    _save_unlocked(mem)

@_with_lock
def list_learning_queue() -> List[Dict[str, Any]]:
    mem = _load_unlocked()
    return list(mem.get("learning_queue", []))

@_with_lock
def pop_learning_queue() -> Optional[Dict[str, Any]]:
    """Pop the oldest queued topic from the queue."""
    mem = _load_unlocked()
    q = mem.get("learning_queue", [])
    idx = None
    for i, item in enumerate(q):
        if item.get("status") == "queued":
            idx = i
            break
    if idx is None:
        return None
    item = q.pop(idx)
    _save_unlocked(mem)
    return item

@_with_lock
def add_knowledge(
    topic: str,
    summary: str,
    sources: Optional[List[str]] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    mem = _load_unlocked()
    mem["knowledge"].append({
        "ts": _utc_now(),
        "topic": topic,
        "summary": summary,
        "sources": sources or [],
        "meta": meta or {},
    })
    _save_unlocked(mem)

# Back-compat for earlier code that expects this name
def add_learning_summary(
    topic: str,
    summary: str,
    sources: Optional[List[str]] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    """Backward-compat wrapper for add_knowledge."""
    add_knowledge(topic, summary, sources, meta)
    # Also drop a note so it's visible in quick tails
    append_note(f"LEARNED: {topic}", tags=["learn"])

@_with_lock
def log_error(
    context: str,
    message: str,
    correct_answer: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Record mistakes so the system can review & avoid repeats later."""
    mem = _load_unlocked()
    mem["errors"].append({
        "ts": _utc_now(),
        "context": context,
        "message": message,
        "correct_answer": correct_answer,
        "extra": extra or {},
    })
    _save_unlocked(mem)

# Convenience: store a Q/A pair when the system had to research
def remember_answer(
    question: str,
    answer: str,
    sources: Optional[List[str]] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    """Record an answer we had to look up as knowledge."""
    add_knowledge(topic=question, summary=answer, sources=sources, meta=meta)
