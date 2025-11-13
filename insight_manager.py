#!/usr/bin/env python3

"""
Insight / reasoning layer for the Machine Spirit.

This does NOT generate answers. It looks at:

- the user text
- the raw answer from the answer engine
- context flags (like used_teaching)

and returns:

- a confidence label: high / medium / low / error / needs_teaching
- some internal flags for logging
"""


from typing import Any, Dict


class InsightManager:
    def analyze(self, user_text: str, raw_answer: str, context: Dict[str, Any]) -> Dict[str, Any]:
        text = (raw_answer or "").strip()
        lowered = text.lower()

        used_teaching = bool(context.get("used_teaching"))

        # 1) If answer clearly comes from a taught explanation, trust it more
        if used_teaching:
            confidence = "high"
            needs_teaching = False
            needs_research = False

        # 2) If it hit a generic fallback line, confidence is low
        elif "i do not have a taught answer for that yet" in lowered:
            confidence = "needs_teaching"
            needs_teaching = True
            needs_research = True

        # 3) If it mentions an internal error, mark as error
        elif "error while calling local answer engine" in lowered:
            confidence = "error"
            needs_teaching = False
            needs_research = True

        # 4) Otherwise, medium by default
        else:
            confidence = "medium"
            needs_teaching = False
            needs_research = False

        return {
            "confidence": confidence,
            "needs_teaching": needs_teaching,
            "needs_research": needs_research,
        }
