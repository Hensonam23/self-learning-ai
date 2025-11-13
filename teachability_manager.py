#!/usr/bin/env python3

"""
Teachability manager for the Machine Spirit.

Now backed by the structured MemoryManager, instead of writing directly
to data/local_knowledge.json.

Responsibilities:
- Normalize questions.
- Record user corrections ("No, that's wrong...")
- Look up canonical explanations for questions.
"""

from typing import Dict, Optional

from memory_manager import MemoryManager, normalize_question, _strip_leading_markers  # type: ignore


class TeachabilityManager:
    def __init__(self) -> None:
        # MemoryManager handles file IO and categories
        self.mem = MemoryManager()

    # ------------------------------------------------------------------ #
    # Public API used by the Brain
    # ------------------------------------------------------------------ #

    def lookup(self, user_text: str) -> Optional[Dict[str, str]]:
        """
        Try to find a canonical explanation for this question.
        """
        cat, canon = self.mem.get(user_text)
        if not canon:
            return None

        norm = normalize_question(user_text)
        return {
            "question": norm,
            "canonical_explanation": canon,
            "category": cat,
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
