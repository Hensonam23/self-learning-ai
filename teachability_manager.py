#!/usr/bin/env python3

"""
Teachability manager for the Machine Spirit.

Now backed by the structured MemoryManager, instead of writing directly
to data/local_knowledge.json.

Responsibilities:
- Normalize questions.
- Record user corrections ("No, that's wrong...")
- Look up canonical explanations for questions, including fuzzy
  matching when there is no exact match.
"""

from typing import Dict, Optional

from memory_manager import (
    MemoryManager,
    normalize_question,
    _strip_leading_markers,  # type: ignore
)


class TeachabilityManager:
    def __init__(self) -> None:
        # MemoryManager handles file IO, categories, and fuzzy search
        self.mem = MemoryManager()

    # ------------------------------------------------------------------ #
    # Public API used by the Brain
    # ------------------------------------------------------------------ #

    def lookup(self, user_text: str) -> Optional[Dict[str, str]]:
        """
        Try to find a canonical explanation for this question.

        Strategy:
        - First, try exact match via MemoryManager.get().
        - If not found, try fuzzy search via MemoryManager.search_similar().
        """
        # 1) Exact match
        cat, canon = self.mem.get(user_text)
        if canon:
            norm = normalize_question(user_text)
            return {
                "question": norm,
                "canonical_explanation": canon,
                "category": cat,
                "from_fuzzy": "false",
            }

        # 2) Fuzzy match
        matches = self.mem.search_similar(user_text, limit=3)
        if not matches:
            return None

        best = matches[0]
        score = float(best.get("score", 0.0))

        # With the new overlap-based score, 0.5 means at least half of
        # the shorter question's tokens overlap with a stored one.
        if score < 0.5:
            return None

        return {
            "question": normalize_question(user_text),
            "canonical_explanation": best["explanation"],
            "category": best.get("category"),
            "matched_question": best.get("question"),
            "match_score": str(score),
            "from_fuzzy": "true",
        }

    def record_correction(
        self,
        previous_question: Optional[str],
        previous_answer: Optional[str],
        user_message: str,
    ) -> Optional[Dict[str, str]]:
        """
        Detect a correction pattern like:

            "> No, that's wrong. <explanation>"

        and store <explanation> as the canonical explanation for the
        previous question.

        If the message does NOT start with a known correction phrase
        (after stripping leading '>'), this does nothing and returns None.
        """
        if not previous_question:
            return None

        raw_msg = user_message.strip()
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

        explanation = clean_msg[len(matched_prefix):].strip()
        if not explanation:
            return None

        # Let MemoryManager choose category and store it
        category = self.mem.set(previous_question, explanation)

        norm_q = normalize_question(previous_question)
        return {
            "question": norm_q,
            "canonical_explanation": explanation,
            "category": category,
        }
