#!/usr/bin/env python3

"""
Insight engine for the Machine Spirit.

This module analyzes messages and determines whether a question
should be marked for deeper research by the evolution loop.

Right now the logic is simple but can be expanded later.
"""

from __future__ import annotations
from typing import Dict


def analyze_message(question: str, answer: str) -> Dict[str, bool]:
    """
    Determine whether the system should research this topic.

    Current logic:
    - If the fallback/local answer looks generic or weak,
      mark it for research.
    - If the question contains "explain", "how", "what is", or
      other info-seeking phrases, mark it as potentially
      research-worthy unless memory had a strong answer.
    """

    q = question.lower().strip()
    a = answer.lower().strip()

    # Words that typically indicate deeper knowledge questions
    triggers = [
        "explain",
        "how do",
        "how does",
        "how is",
        "what is",
        "why is",
        "difference between",
        "in simple words",
        "overview",
        "summarize",
        "tell me about",
        "teach me",
    ]

    # Phrase that typically indicates the fallback engine failed
    weak_markers = [
        "i am online but",
        "core systems are online",
        "greetings.",
        "i do not",
        "i'm not sure",
        "i am not sure",
        "i do not have",
        "unknown",
        "not clearly described",
    ]

    # If fallback answer is weak → definitely research
    for w in weak_markers:
        if w in a:
            return {"needs_research": True}

    # If question looks informational → likely research
    for trig in triggers:
        if q.startswith(trig) or trig in q:
            return {"needs_research": True}

    # Default: no research required
    return {"needs_research": False}
