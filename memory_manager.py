#!/usr/bin/env python3

"""
Structured memory manager for the Machine Spirit.

Responsibilities:
- Store knowledge in category files under data/memory/
- Provide a single API to get/set knowledge by question
- Keep a combined mirror in data/local_knowledge.json for debugging

Categories (initial set):
- general
- pc_hardware
- warhammer_lore
- user_prefs

This file also exposes normalize_question() so all components use the
same normalization logic.
"""

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
    Strip leading '>' markers and spaces. Used for both questions and
    corrections so that your CLI style still works.
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
    - collapse internal whitespace
    """
    t = _strip_leading_markers(text).lower()
    return " ".join(t.split())


class MemoryManager:
    """
    Category-based memory system.

    Public API:
      - get(question) -> (category, explanation) or (None, None)
      - set(question, explanation, category=None)

    Internally:
      - Keeps per-category dicts: {normalized_question -> explanation}
      - On init, imports any legacy entries from data/local_knowledge.json
        into categories.
      - Always writes a combined mirror to data/local_knowledge.json
        so the file stays useful for inspection.
    """

    CATEGORY_FILES = {
        "general": os.path.join(BASE_DIR, "general.json"),
        "pc_hardware": os.path.join(BASE_DIR, "pc_hardware.json"),
        "warhammer_lore": os.path.join(BASE_DIR, "warhammer_lore.json"),
        "user_prefs": os.path.join(BASE_DIR, "user_prefs.json"),
    }

    def __init__(self) -> None:
        # Load category stores
        self.store: Dict[str, Dict[str, str]] = {}
        for cat, path in self.CATEGORY_FILES.items():
            self.store[cat] = _load_json_dict(path)

        # Import legacy flat knowledge if present
        legacy = _load_json_dict(LOCAL_KNOWLEDGE_PATH)
        self._import_legacy(legacy)

        # Save everything (also refresh the combined mirror)
        self._save_all()

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _import_legacy(self, legacy: Dict[str, Any]) -> None:
        """
        Bring in any existing entries from data/local_knowledge.json into
        categories. We only add entries that don't already exist in any
        category, to avoid duplication.
        """
        if not legacy:
            return

        for raw_q, v in legacy.items():
            if not isinstance(raw_q, str) or not isinstance(v, str):
                continue

            norm_q = normalize_question(raw_q)
            if not norm_q:
                continue

            # Skip if already present anywhere
            if self._find_existing_category(norm_q) is not None:
                continue

            cat = self._auto_category(raw_q, v)
            self.store.setdefault(cat, {})[norm_q] = v

    def _find_existing_category(self, norm_q: str) -> Optional[str]:
        for cat, data in self.store.items():
            if norm_q in data:
                return cat
        return None

    def _auto_category(self, question: str, answer: Optional[str] = None) -> str:
        """
        Very simple heuristic categorization based on question text.
        This does NOT need to be perfect; it's just a first draft.
        """
        q = question.lower()

        # PC / gaming / hardware topics
        pc_words = [
            "pc", "gpu", "cpu", "ram", "monitor", "fps", "gaming",
            "graphics card", "ryzen", "intel", "nvidia", "keyboard", "mouse",
        ]
        if any(w in q for w in pc_words):
            return "pc_hardware"

        # Warhammer topics
        wh_words = [
            "warhammer", "40k", "40,000", "imperium", "space marine",
            "tech-priest", "omnnissiah", "adeptus", "primarch",
        ]
        if any(w in q for w in wh_words):
            return "warhammer_lore"

        # Preferences / personal stuff
        pref_words = [
            "favorite", "favourite", "like to", "i like", "i prefer",
            "snack", "food", "drink", "what do i like",
        ]
        if any(w in q for w in pref_words):
            return "user_prefs"

        return "general"

    def _save_all(self) -> None:
        """
        Save each category file and the combined mirror.
        """
        # Save category files
        for cat, path in self.CATEGORY_FILES.items():
            data = self.store.get(cat, {})
            _save_json_dict(path, data)

        # Build combined mirror
        combined: Dict[str, str] = {}
        for cat, data in self.store.items():
            for k, v in data.items():
                # If duplicate keys across categories, keep the first one.
                if k not in combined and isinstance(v, str):
                    combined[k] = v

        _save_json_dict(LOCAL_KNOWLEDGE_PATH, combined)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def get(self, question: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Return (category, explanation) or (None, None) if not found.
        """
        norm_q = normalize_question(question)
        if not norm_q:
            return None, None

        for cat, data in self.store.items():
            if norm_q in data:
                return cat, data[norm_q]

        return None, None

    def set(self, question: str, explanation: str, category: Optional[str] = None) -> str:
        """
        Store or update a canonical explanation for a question.

        Returns the category it ended up in.
        """
        norm_q = normalize_question(question)
        if not norm_q:
            # just dump into general under a placeholder key
            norm_q = "unknown"

        if category is None:
            category = self._auto_category(question, explanation)

        if category not in self.store:
            # fallback to general if unknown category requested
            category = "general"

        self.store.setdefault(category, {})[norm_q] = explanation
        self._save_all()
        return category
