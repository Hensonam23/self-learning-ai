import json
import os
import time
from typing import Any, Dict, List


def _safe_load(path: str) -> Dict[str, Any]:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return {"notes": [], "learned_topics": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
    except Exception:
        return {"notes": [], "learned_topics": []}
    if isinstance(obj, list):
        obj = {"notes": obj, "learned_topics": []}
    if not isinstance(obj, dict):
        obj = {"notes": [], "learned_topics": []}
    obj.setdefault("notes", [])
    obj.setdefault("learned_topics", [])
    return obj


def _safe_write(path: str, obj: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def add_note(path: str, text: str) -> None:
    obj = _safe_load(path)
    obj["notes"].append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "text": text})
    _safe_write(path, obj)


def add_learning_summary(path: str, topic: str, items: List[Dict[str, str]]) -> None:
    obj = _safe_load(path)
    obj["learned_topics"].append(
        {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "topic": topic, "items": items}
    )
    _safe_write(path, obj)
