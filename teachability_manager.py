#!/usr/bin/env python3

"""
Teachability manager for the Machine Spirit.

Responsible for:
- Loading/saving local_knowledge.json.
- Normalizing questions.
- Recording user corrections.
- Looking up canonical explanations for questions.

This version:
- Cleans up old messy keys (prompt garbage).
- ONLY records corrections when the user message clearly starts with a
  correction phrase like "No, that's wrong." (after stripping leading '>').
"""

import json
import os
from typing import Any, Dict, Optional


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
    Strip leading '>' markers and spaces. So:

      '> No, that is wrong...' -> 'No, that is wrong...'
      '>>  no, that is wrong'  -> 'no, that is wrong'
    """
    t = text.strip()
    while t.startswith(">"):
        t = t[1:].lstrip()
    return t


def normalize_question(text: str) -> str:
    """
    Shared normalization for questions:

    - strip whitespace
    - lowercase
    - strip leading '>' markers
    - collapse internal whitespace
    """
    t = _strip_leading_markers(text).lower()
    return " ".join(t.split())


class TeachabilityManager:
    def __init__(self, path: str = LOCAL_KNOWLEDGE_PATH) -> None:
        self.path = path
        raw = _load_json_dict(self.path)
        self.knowledge: Dict[str, str] = self._clean_and_normalize(raw)
        # Save cleaned version immediately
        self._save()

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _clean_and_normalize(self, raw: Dict[str, Any]) -> Dict[str, str]:
        """
        Take the raw dict from local_knowledge.json and turn it into a clean
        mapping:

            normalized_question -> canonical_explanation (string)

        - Drop keys/values that look like old prompt garbage.
        - If multiple entries map to the same normalized question, keep
          the shorter value (tends to keep simpler explanations).
        """
        store: Dict[str, str] = {}

        for k, v in raw.items():
            if not isinstance(k, str) or not isinstance(v, str):
                continue

            lk = k.lower()
            lv = v.lower()

            # Filter obviously bad keys/values from old bugs
            if "you were corrected by the user previously" in lk:
                continue
            if "source of truth:" in lk:
                continue
            if "you were corrected by the user previously" in lv:
                continue
            if "source of truth:" in lv:
                continue

            norm = normalize_question(k)
            if not norm:
                continue

            if norm in store:
                existing = store[norm]
                if len(v) < len(existing):
                    store[norm] = v
            else:
                store[norm] = v

        return store

    def _save(self) -> None:
        _save_json_dict(self.path, self.knowledge)

    # ------------------------------------------------------------------ #
    # Public API used by the Brain
    # ------------------------------------------------------------------ #

    def lookup(self, user_text: str) -> Optional[Dict[str, str]]:
        """
        Try to find a canonical explanation for this question.
        """
        norm = normalize_question(user_text)
        canon = self.knowledge.get(norm)
        if not canon:
            return None

        return {
            "question": norm,
            "canonical_explanation": canon,
        }

    def record_correction(
        self,
        previous_question: Optional[str],
        previous_answer: Optional[str],
        user_message: str,
    ) -> Optional[Dict[str, str]]:
        """
        Detect a correction pattern like:

            "No, that's wrong. <explanation>"

        and store <explanation> as the canonical explanation for the
        previous question.

        If the message does NOT start with a known correction phrase
        (after stripping leading '>'), this does nothing and returns None.
        """
        if not previous_question:
            return None

        raw_msg = user_message.strip()
        # For prefix detection, strip leading '>' markers
        clean_msg = _strip_leading_markers(raw_msg)
        lower_clean = clean_msg.lower()

        prefix_candidates = [
            "no, that's wrong.",
            "no that's wrong.",
            "that's wrong.",
            "no, that is wrong.",
            "no that is wrong.",
        ]

        matched_prefix = None
        for pref in prefix_candidates:
            if lower_clean.startswith(pref):
                matched_prefix = pref
                break

        if matched_prefix is None:
            # Not a correction; ignore
            return None

        # Find the prefix in the CLEAN version, then map to the same slice
        # in the CLEAN version itself for the explanation.
        explanation = clean_msg[len(matched_prefix):].strip()
        if not explanation:
            return None

        norm_q = normalize_question(previous_question)
        self.knowledge[norm_q] = explanation
        self._save()

        return {
            "question": norm_q,
            "canonical_explanation": explanation,
        }
