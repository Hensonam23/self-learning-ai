# answer_engine.py
from __future__ import annotations
import json, os, re, socket, time, math, ast, datetime as dt
from typing import Optional, List

try:
    from storage.memory import queue_learning  # type: ignore
except Exception:
    def queue_learning(_topic: str) -> None:
        pass

MEM_PATH = os.path.expanduser("~/self-learning-ai/memory.json")

# ---------- memory ----------
def _load_mem() -> dict:
    try:
        with open(MEM_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"notes": [], "sessions": [], "knowledge": [], "learning_queue": [], "profile": {}}

def _save_mem(mem: dict) -> None:
    tmp = MEM_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(mem, f, indent=2, ensure_ascii=False)
        os.replace(tmp, MEM_PATH)
    except Exception:
        pass

def _remember_qa(q: str, a: str) -> None:
    mem = _load_mem()
    mem.setdefault("notes", []).append({
        "t": int(time.time()),
        "q": q.strip(),
        "a": a.strip()
    })
    if len(mem["notes"]) > 500:
        mem["notes"] = mem["notes"][-500:]
    _save_mem(mem)

def set_user_name(name: str) -> None:
    name = name.strip()
    if not name:
        return
    mem = _load_mem()
    prof = mem.setdefault("profile", {})
    if prof.get("user_name") != name:
        prof["user_name"] = name
        _save_mem(mem)

def get_user_name() -> Optional[str]:
    mem = _load_mem()
    return mem.get("profile", {}).get("user_name")

# ---------- utilities ----------
def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def _now_local() -> dt.datetime:
    return dt.datetime.now()

def _format_time(t: dt.datetime) -> str:
    return t.strftime("%I:%M %p").lstrip("0")

def _format_date(t: dt.datetime) -> str:
    return t.strftime("%A, %B %d, %Y").replace(" 0", " ")

def _normalize(txt: str) -> str:
    return re.sub(r"\s+", " ", (txt or "").strip().lower())

# safe math
_ALLOWED_NODES = {
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Num, ast.Constant,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow, ast.USub, ast.UAdd,
    ast.Load, ast.Name
}
_ALLOWED_NAMES = {"pi": math.pi, "e": math.e}

def _safe_eval_expr(expr: str) -> Optional[float]:
    try:
        tree = ast.parse(expr, mode="eval")
        for node in ast.walk(tree):
            if type(node) not in _ALLOWED_NODES:
                return None
            if isinstance(node, ast.Name) and node.id not in _ALLOWED_NAMES:
                return None
        val = eval(compile(tree, "<expr>", "eval"), {"__builtins__": {}}, _ALLOWED_NAMES)
        try:
            return float(val)
        except Exception:
            return None
    except Exception:
        return None

def _maybe_math(q: str) -> Optional[str]:
    qn = _normalize(q)
    m = re.search(r"(?:what\s+is|what's)\s+(.+)$", qn)
    expr = m.group(1) if m else qn
    expr = expr.replace("times", "*").replace("x", "*").replace("plus", "+").replace("minus", "-")
    expr = expr.replace("divided by", "/").replace("over", "/").replace("into", "*").replace("^", "**")
    if not re.search(r"\d", expr) or not re.search(r"[\+\-\*\/\%]|\*\*", expr):
        return None
    val = _safe_eval_expr(expr)
    if val is None:
        return None
    return str(int(val)) if abs(val - int(val)) < 1e-12 else str(round(val, 6))

# ---------- small facts ----------
_FACTS = {
    "warhammer 40k": "Warhammer 40,000 is a sci-fi tabletop wargame by Games Workshop set in a grimdark far future. Players build and paint miniatures and battle using dice-driven rules.",
    "warhammer40k": "Warhammer 40,000 is a sci-fi tabletop wargame by Games Workshop set in a grimdark far future. Players build and paint miniatures and battle using dice-driven rules.",
    "warhammer": "Warhammer usually refers to Games Workshop’s tabletop games: Warhammer 40,000 (sci-fi) and Warhammer Age of Sigmar (fantasy).",
}

def _facts(qn: str) -> Optional[str]:
    for k, v in _FACTS.items():
        if k in qn:
            return v
    return None

# ---------- memory search ----------
def _search_knowledge(q: str) -> Optional[str]:
    term = _normalize(q)
    mem = _load_mem()
    for item in mem.get("knowledge", []):
        topic = _normalize(item.get("topic", ""))
        if term in topic:
            return item.get("summary")
    return None

# ---------- responders ----------
def _greeting() -> str:
    return "Hey—I’m here and listening."

def _who_are_you() -> str:
    return "I’m the Machine Spirit—your on-device assistant. I listen, think, and keep improving from what we do here."

def _status() -> str:
    return "Nominal systems online and paying attention."

def _time_now() -> str:
    return _format_time(_now_local())

def _date_today() -> str:
    return _format_date(_now_local())

def _day_today() -> str:
    return _now_local().strftime("%A")

def _where_am_i() -> str:
    ip = _local_ip()
    return f"You’re talking to me at {ip} on your local network. If you enable network/location services, I can be more specific."

def _name_logic(q: str) -> Optional[str]:
    m = re.search(r"\bmy\s+name\s+is\s+(.+)", q, re.I)
    if m:
        name = re.sub(r"[^\w\s\-'.]", "", m.group(1)).strip()
        if name:
            set_user_name(name)
            return f"Got it. I’ll call you {name}."
    if re.search(r"\bwhat(?:'s|\s+is)\s+my\s+name\b", q, re.I):
        n = get_user_name()
        return f"Your name is {n}." if n else "You haven’t told me yet. Say: “my name is <name>”."
    if re.search(r"\bwhat(?:'s|\s+is)\s+your\s+name\b|\byour\s+name\b", q, re.I):
        return "Machine Spirit."
    return None

def _small_talk(qn: str) -> Optional[str]:
    if qn in {"hi","hello","hey","yo","hiya","sup"} or re.fullmatch(r"(hi|hello|hey)[\.\!\?]?", qn):
        return _greeting()
    if "who are you" in qn or "what are you" in qn:
        return _who_are_you()
    if "how are you" in qn or "status" in qn:
        return _status()
    if "purpose" in qn or "mission" in qn:
        return "Help you think and execute: turn intent into plans, answers, and actions—and improve from each session."
    return None

def _default_answer(q: str) -> str:
    remembered = _search_knowledge(q)
    if remembered:
        return remembered
    queue_learning(q)
    return "I haven't learned about that yet. Share a detail and I'll remember it for next time."

def respond(user_text: str) -> str:
    text = (user_text or "").strip()
    if not text:
        return "I’m listening."

    named = _name_logic(text)
    if named:
        _remember_qa(text, named)
        return named

    qn = _normalize(text)

    ans = _small_talk(qn)
    if ans:
        _remember_qa(text, ans)
        return ans

    if re.search(r"\btime\b", qn) and ("what" in qn or "current" in qn):
        ans = _time_now(); _remember_qa(text, ans); return ans
    if re.search(r"\bdate\b", qn):
        ans = _date_today(); _remember_qa(text, ans); return ans
    if "what day" in qn:
        ans = _day_today(); _remember_qa(text, ans); return ans
    if "where am i" in qn:
        ans = _where_am_i(); _remember_qa(text, ans); return ans

    math_ans = _maybe_math(text)
    if math_ans is not None:
        _remember_qa(text, math_ans)
        return math_ans

    f = _facts(qn)
    if f:
        _remember_qa(text, f)
        return f

    ans = _default_answer(text)
    _remember_qa(text, ans)
    return ans
