from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_key(text: str) -> str:
    # Simple normalization so "OSI Model?" and "osi model" match.
    key = (text or "").strip().lower()
    while key.endswith("?"):
        key = key[:-1].strip()
    return key


def load_base_knowledge(path: Path) -> dict:
    if not path.exists():
        return {"items": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        # If file is corrupted, don't crash the brain.
        return {"items": {}}


def save_base_knowledge(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def get_answer(store: dict, question: str) -> str | None:
    key = normalize_key(question)
    item = store.get("items", {}).get(key)
    if not item:
        return None
    return item.get("answer")


def teach_answer(store: dict, question: str, answer: str, source: str = "user") -> None:
    key = normalize_key(question)
    store.setdefault("items", {})
    store["items"][key] = {
        "question": question.strip(),
        "answer": answer.strip(),
        "source": source,
        "updated_at": _utc_now_iso(),
    }
