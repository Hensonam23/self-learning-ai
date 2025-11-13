#!/usr/bin/env python3

"""
Style / behavior layer for the Machine Spirit.

This runs as a final formatting pass on EVERY answer:

- Keeps responses clean and direct.
- Adds a small Machine Spirit flavor without being over the top.
- Tries to strip out any internal prompt noise if it ever leaks through.
"""

from typing import Any, Dict


class StyleManager:
    def __init__(self) -> None:
        # Persona knobs
        self.persona_prefix = "Machine Spirit: "
        self.max_blank_lines = 2

    # Public API -------------------------------------------------------------

    def format_answer(
        self,
        user_text: str,
        raw_answer: str,
        context: Dict[str, Any],
    ) -> str:
        """
        user_text: the message you sent
        raw_answer: text returned by the answer engine or tools
        context: extra flags like:
            {
              "used_teaching": True/False,
              "confidence": "high"/"medium"/"low"/"needs_teaching"/"error",
              "needs_research": True/False,
              "needs_teaching": True/False,
              "tool": "scan"/"summarize"/"explain_new"/None,
            }
        """
        text = raw_answer or ""

        # 1) Basic cleanup
        text = self._strip_meta_noise(text)
        text = self._collapse_blank_lines(text)

        # 2) Attach persona flavor and confidence hints
        text = self._apply_persona(text, context)

        # 3) Final trim
        return text.strip()

    # Internal helpers -------------------------------------------------------

    def _strip_meta_noise(self, text: str) -> str:
        """
        If the underlying engine ever leaks "source of truth"
        or "Now answer the user's new question" back out, strip it away.
        """
        lowered = text.lower()
        if "source of truth:" in lowered or "now answer the user's new question" in lowered:
            lines = []
            for line in text.splitlines():
                l = line.lower()
                if "source of truth:" in l:
                    continue
                if "now answer the user's new question" in l:
                    continue
                lines.append(line)
            text = "\n".join(lines)

        return text

    def _collapse_blank_lines(self, text: str) -> str:
        """
        Collapse multiple blank lines so the answer stays neat.
        """
        lines = text.splitlines()
        new_lines = []
        blank_count = 0

        for line in lines:
            if line.strip() == "":
                blank_count += 1
                if blank_count <= self.max_blank_lines:
                    new_lines.append("")
            else:
                blank_count = 0
                new_lines.append(line)

        return "\n".join(new_lines)

    def _apply_persona(self, text: str, context: Dict[str, Any]) -> str:
        """
        Add a small Machine Spirit flavor. Keep it simple and readable.
        Also reflect confidence and research when they are low/needed.
        """
        stripped = text.lstrip()

        confidence = context.get("confidence", "medium")
        used_teaching = bool(context.get("used_teaching"))
        needs_research = bool(context.get("needs_research"))

        base = stripped

        # Low confidence or needs teaching -> be honest about it
        if confidence in ("low", "needs_teaching", "error"):
            disclaimer = (
                " My analysis may be incomplete. If this seems wrong, correct me and I will update my understanding."
            )
            base = base + disclaimer

        # If we have flagged this for deeper research, mention it
        if needs_research:
            research_note = " I have also marked this topic for deeper research so I can improve my answer over time."
            base = base + research_note

        # Decide how to prefix
        lowered = base.lower()
        if lowered.startswith(("machine spirit:", "greetings", "core systems")):
            # Already has persona feel; just return cleaned text.
            return base

        # If this answer used a taught explanation, be confidently in-character.
        if used_teaching or confidence == "high":
            return f"{self.persona_prefix}{base}"

        # Generic / medium answers: still in-character but neutral
        return f"{self.persona_prefix}{base}"
