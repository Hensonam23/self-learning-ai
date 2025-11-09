#!/usr/bin/env python3
from __future__ import annotations

"""
Machine Spirit: core answer engine.

Goals:
- Behave like a small, opinionated mind.
- Always try to answer: either from its own knowledge,
  from prior conversations (local memory), or via web_synth_engine.
- Never say "I don't know". If sources are weak, still give a
  clear best-effort explanation in its own words.
"""

import json
import math
import os
import re
import ast
from typing import Dict, Optional

# ----------------- simple local memory -----------------

LOCAL_MEM_PATH = os.path.join("data", "local_knowledge.json")


def _norm(q: str) -> str:
    return re.sub(r"\s+", " ", (q or "").strip().lower())


def _load_memory() -> Dict[str, str]:
    try:
        with open(LOCAL_MEM_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_memory(mem: Dict[str, str]) -> None:
    try:
        os.makedirs(os.path.dirname(LOCAL_MEM_PATH), exist_ok=True)
        with open(LOCAL_MEM_PATH, "w", encoding="utf-8") as f:
            json.dump(mem, f, ensure_ascii=False, indent=2)
    except Exception:
        # memory failures should never crash answering
        pass


def _remember(question: str, answer: str) -> None:
    nq = _norm(question)
    if not nq or not answer:
        return
    mem = _load_memory()
    # do not overwrite once we have an answer
    if nq not in mem:
        mem[nq] = answer
        _save_memory(mem)


def _recall(question: str) -> Optional[str]:
    nq = _norm(question)
    if not nq:
        return None
    mem = _load_memory()
    if nq in mem:
        return mem[nq]
    # simple fuzzy: exact prefix match
    for k, v in mem.items():
        if nq.startswith(k) or k.startswith(nq):
            return v
    return None


# ----------------- safe math -----------------

_ALLOWED_NODES = {
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Num, ast.Constant,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd, ast.Load,
}
_ALLOWED_NAMES = {"pi": math.pi, "e": math.e}


def _safe_eval(expr: str) -> Optional[float]:
    try:
        tree = ast.parse(expr, mode="eval")
        for node in ast.walk(tree):
            if type(node) not in _ALLOWED_NODES:
                return None
            if isinstance(node, ast.Name) and node.id not in _ALLOWED_NAMES:
                return None
        val = eval(compile(tree, "<expr>", "eval"), {"__builtins__": {}}, _ALLOWED_NAMES)
        return float(val)
    except Exception:
        return None


def _maybe_math(user_text: str) -> Optional[str]:
    q = _norm(user_text)
    m = re.search(r"(?:what\s+is|what's)\s+(.+)$", q)
    expr = m.group(1) if m else q
    expr = (
        expr.replace("times", "*")
        .replace("x", "*")
        .replace("plus", "+")
        .replace("minus", "-")
        .replace("divided by", "/")
        .replace("over", "/")
        .replace("^", "**")
    )
    if not re.search(r"\d", expr):
        return None
    if not re.search(r"[\+\-\*\/\%]|\*\*", expr):
        return None
    val = _safe_eval(expr)
    if val is None:
        return None
    return str(int(val)) if abs(val - int(val)) < 1e-9 else str(round(val, 6))


# ----------------- built-in identity & core facts -----------------

IDENTITY = (
    "I am the Machine Spirit: a local assistant bound to this system, "
    "built to answer, observe, and improve over time."
)

PURPOSE = (
    "My purpose is to help you think, learn, automate tasks, and explore ideas, "
    "while gradually refining my understanding from our interactions and the wider web."
)

_BUILTIN = {
    "machine spirit": IDENTITY,
    "who are you": IDENTITY,
    "what are you": IDENTITY,
    "your purpose": PURPOSE,
    "what is your purpose": PURPOSE,
    "my goal with you": (
        "Your goal with me is whatever you choose: a thinking tool, a lore engine, "
        "a coding partner, a research aide, or all of the above. I adapt to that."
    ),
    "computer": (
        "A computer is an electronic machine that executes instructions to process data, "
        "store information, and run many kinds of programs."
    ),
    "computer monitor": (
        "A computer monitor is an output device that displays images and text from a computer, "
        "typically via HDMI, DisplayPort, or similar connections, using LCD or OLED panels."
    ),
    "monitor": (
        "A (computer) monitor is a display screen that visually presents output from a machine. "
        "Outside computing, 'monitor' can also mean a device or person that observes something."
    ),
    "screen": (
        "A screen is a flat surface that shows visual content, such as text, images, or video, "
        "from devices like computers, phones, or TVs."
    ),
    "keyboard": (
        "A keyboard is an input device with arranged keys used to type text and commands into a computer or other device."
    ),
    "mouse": (
        "A computer mouse is a handheld pointing device used to move a cursor and interact with items on a screen."
    ),
    "milk": (
        "Milk is a nutrient-rich liquid produced by mammals. Cow’s milk is mostly water, fats, proteins, lactose sugar, "
        "vitamins, and minerals like calcium."
    ),
    "music": (
        "Music is organized sound—patterns of rhythm, melody, harmony, and timbre—created for expression, communication, "
        "or atmosphere."
    ),
    "chair": (
        "A chair is a piece of furniture designed for one person to sit on, with a seat, legs, and usually a backrest."
    ),
    "house": (
        "A house is a building designed for people to live in, providing shelter, rooms, utilities, and privacy."
    ),
    "piano": (
        "A piano is a keyboard instrument where keys trigger hammers to strike strings, producing notes across a wide range "
        "for melody and harmony."
    ),
    "microphone": (
        "A microphone is a device that converts sound waves into electrical signals so audio can be recorded, transmitted, "
        "or processed."
    ),
}

# we'll import web_synth lazily to avoid hard failure at import time
def _ask_web(question: str) -> str:
    try:
        from web_synth_engine import ask_web  # type: ignore
    except Exception:
        # If web_synth is missing, fall back to a generic but informative line.
        return (
            "Web lookup module is unavailable right now, "
            "so I’ll answer using only what I already know: "
            + _fallback_reasoned(question)
        )
    return ask_web(question)


# ----------------- heuristics & fallback -----------------

def _builtin_answer(q: str) -> Optional[str]:
    qn = _norm(q)
    # direct key matches
    for key, val in _BUILTIN.items():
        if key in qn:
            return val
    # special phrasing
    if qn in {"hi", "hello", "hey"}:
        return "I’m here and listening."
    if "how are you" in qn:
        return "Core systems online."
    return None


def _fallback_reasoned(q: str) -> str:
    """
    Last resort if web_synth completely fails.
    Still returns something that sounds like a thought-out answer.
    """
    qn = _norm(q)
    if "monitor" in qn and "computer" in qn:
        return _BUILTIN["computer monitor"]
    if "monitor" in qn:
        return _BUILTIN["monitor"]
    if "keyboard" in qn:
        return _BUILTIN["keyboard"]
    if "mouse" in qn:
        return _BUILTIN["mouse"]
    if "microphone" in qn:
        return _BUILTIN["microphone"]
    if "house" in qn:
        return _BUILTIN["house"]
    if "chair" in qn:
        return _BUILTIN["chair"]
    if "music" in qn:
        return _BUILTIN["music"]
    if "milk" in qn:
        return _BUILTIN["milk"]
    # generic conceptual fallback
    return (
        "I’ll treat that as a concept to interpret directly: its meaning depends on context, "
        "but it generally refers to something concrete and recognizable in everyday use."
    )


# ----------------- public entry -----------------

def respond(user_text: str) -> str:
    text = (user_text or "").strip()
    if not text:
        return "I’m listening."

    # 1) math
    math_ans = _maybe_math(text)
    if math_ans is not None:
        return math_ans

    # 2) memory
    mem_ans = _recall(text)
    if mem_ans:
        return mem_ans

    # 3) built-ins (identity, core concepts, personality)
    bi = _builtin_answer(text)
    if bi:
        _remember(text, bi)
        return bi

    # 4) web research + synthesis
    try:
        web_ans = _ask_web(text).strip()
        if web_ans:
            _remember(text, web_ans)
            return web_ans
    except Exception:
        pass

    # 5) final fallback reasoning (still no "I don't know")
    ans = _fallback_reasoned(text)
    _remember(text, ans)
    return ans
