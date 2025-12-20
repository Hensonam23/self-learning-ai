#!/usr/bin/env python3

"""
Structured memory manager for the Machine Spirit.

Responsibilities:
- Store knowledge in category files under data/memory/
- Provide a single API to get/set knowledge by question
- Keep a combined mirror in data/local_knowledge.json for debugging

Categories:
- general
- pc_hardware
- warhammer_lore
- user_prefs

Also exposes:
- normalize_question()  -> shared normalization logic
- search_similar()      -> fuzzy lookup over known questions
"""

from __future__ import annotations
import json
import os
from typing import Any, Dict, Optional, Tuple, List


BASE_DIR = "data/memory"
LOCAL_KNOWLEDGE_PATH = "data/local_knowledge.json"


def _ensure_dir(path: str) -> None:
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)


def _load_json_dict(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        backup = f"{path}.corrupt"
        try:
            os.replace(path, backup)
        except Exception:
            pass
        return {}


def _save_json_dict(path: str, data: Dict[str, Any]) -> None:
    _ensure_dir(path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _strip_leading_markers(text: str) -> str:
    """
    Strip leading '>' markers and spaces (for CLI-style input).
    """
    t = text.strip()
    while t.startswith(">"):
        t = t[1:].lstrip()
    return t


def normalize_question(text: str) -> str:
    """
    Shared normalization for questions:

    - strip whitespace
    - remove leading '>' markers
    - lowercase
    - strip trailing punctuation like .?!,:;"'
    - collapse internal whitespace

    So variations like:
      "Explain the OSI model in simple words."
      "> EXPLAIN THE OSI MODEL IN SIMPLE WORDS?!"
    all normalize to the same key.
    """
    t = _strip_leading_markers(text).lower().strip()

    while t and t[-1] in ".?!,:;\"'":
        t = t[:-1].rstrip()

    return " ".join(t.split())


def _tokenize(norm_q: str) -> List[str]:
    return norm_q.split()


class MemoryManager:
    """
    Category-based memory system.

    Public API:
      - get(question) -> (category, explanation) or (None, None)
      - set(question, explanation, category=None) -> category
      - search_similar(question, limit=3) -> list of matches
    """

    CATEGORY_FILES = {
        "general": os.path.join(BASE_DIR, "general.json"),
        "pc_hardware": os.path.join(BASE_DIR, "pc_hardware.json"),
        "warhammer_lore": os.path.join(BASE_DIR, "warhammer_lore.json"),
        "user_prefs": os.path.join(BASE_DIR, "user_prefs.json"),
    }

    def __init__(self) -> None:
        self.store: Dict[str, Dict[str, str]] = {}
        for cat in self.CATEGORY_FILES:
            self.store[cat] = {}

        # Load category files
        for cat, path in self.CATEGORY_FILES.items():
            raw = _load_json_dict(path)
            for k, v in raw.items():
                self._add_entry(k, v, preferred_category=cat)

        # Import legacy flat local_knowledge.json
        legacy = _load_json_dict(LOCAL_KNOWLEDGE_PATH)
        for k, v in legacy.items():
            self._add_entry(k, v, preferred_category=None)

        self._save_all()

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _add_entry(
        self,
        question: Any,
        explanation: Any,
        preferred_category: Optional[str] = None,
    ) -> None:
        """
        Normalize and insert a single (question, explanation) pair.

        IMPORTANT CHANGE:
        - If the key already exists, we now prefer the *longer* explanation,
          so richer web-learned summaries overwrite tiny placeholder strings
          like 'Greetings. Core systems are online and listening.'
        """
        if not isinstance(question, str) or not isinstance(explanation, str):
            return

        norm_q = normalize_question(question)
        if not norm_q:
            return

        # If it already exists, prefer the longer explanation.
        for cat, data in self.store.items():
            if norm_q in data:
                existing = data[norm_q]
                if len(explanation) > len(existing):
                    data[norm_q] = explanation
                return

        # Choose category
        if preferred_category in self.store:
            cat = preferred_category
        else:
            cat = self._auto_category(question, explanation)

        self.store.setdefault(cat, {})[norm_q] = explanation

    def _auto_category(self, question: str, answer: Optional[str] = None) -> str:
        q = question.lower()

        pc_words = [
            "pc", "gpu", "cpu", "ram", "monitor", "fps", "gaming",
            "graphics card", "ryzen", "intel", "nvidia", "keyboard", "mouse",
        ]
        if any(w in q for w in pc_words):
            return "pc_hardware"

        wh_words = [
            "warhammer", "40k", "40,000", "imperium", "space marine",
            "tech-priest", "omnissiah", "adeptus", "primarch",
        ]
        if any(w in q for w in wh_words):
            return "warhammer_lore"

        pref_words = [
            "favorite", "favourite", "like to", "i like", "i prefer",
            "snack", "food", "drink", "what do i like",
        ]
        if any(w in q for w in pref_words):
            return "user_prefs"

        return "general"

    def _save_all(self) -> None:
        for cat, path in self.CATEGORY_FILES.items():
            data = self.store.get(cat, {})
            _save_json_dict(path, data)

        combined: Dict[str, str] = {}
        for cat, data in self.store.items():
            for k, v in data.items():
                if k not in combined and isinstance(v, str):
                    combined[k] = v

        _save_json_dict(LOCAL_KNOWLEDGE_PATH, combined)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def get(self, question: str) -> Tuple[Optional[str], Optional[str]]:
        norm_q = normalize_question(question)
        if not norm_q:
            return None, None

        for cat, data in self.store.items():
            if norm_q in data:
                return cat, data[norm_q]

        return None, None

    def set(self, question: str, explanation: str, category: Optional[str] = None) -> str:
        if category is not None and category not in self.store:
            category = None

        if category is None:
            category = self._auto_category(question, explanation)

        self._add_entry(question, explanation, preferred_category=category)
        self._save_all()
        return category

    def search_similar(self, question: str, limit: int = 3) -> List[Dict[str, Any]]:
        """
        Fuzzy search using token overlap.

        Score = |intersection| / min(len(target_tokens), len(memory_tokens))

        That means if your shorter phrasing's tokens are all contained in a
        longer stored question, the score will be 1.0.
        """
        norm_target = normalize_question(question)
        if not norm_target:
            return []

        target_tokens = _tokenize(norm_target)
        if not target_tokens:
            return []

        target_set = set(target_tokens)

        matches: List[Dict[str, Any]] = []

        for cat, data in self.store.items():
            for q_norm, explanation in data.items():
                q_tokens = _tokenize(q_norm)
                if not q_tokens:
                    continue

                q_set = set(q_tokens)
                inter = target_set & q_set
                if not inter:
                    continue

                denom = float(min(len(target_set), len(q_set)))
                if denom == 0.0:
                    continue

                score = len(inter) / denom

                matches.append(
                    {
                        "score": score,
                        "category": cat,
                        "question": q_norm,
                        "explanation": explanation,
                    }
                )

        matches.sort(key=lambda m: m["score"], reverse=True)
        return matches[:limit]
