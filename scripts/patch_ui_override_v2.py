from pathlib import Path
import re
import sys

p = Path("ms_ui.py")
txt = p.read_text(encoding="utf-8", errors="replace")

# Find where HTML_TEMPLATE begins so we only patch REAL python
m_html = re.search(r"(?m)^HTML_TEMPLATE\s*=\s*r[\'\"]{3}", txt)
if not m_html:
    print("ERROR: Could not find HTML_TEMPLATE = r\"\"\" in ms_ui.py")
    sys.exit(1)

pre = txt[:m_html.start()]
post = txt[m_html.start():]

lines = pre.splitlines(True)
changed = False

def find_insert_after_model(model_name: str):
    for i, line in enumerate(lines):
        if line.strip() == f"class {model_name}(BaseModel):":
            j = i + 1
            while j < len(lines) and (lines[j].startswith((" ", "\t")) or lines[j].strip() == ""):
                j += 1
            return j
    return None

def find_insert_point():
    idx = find_insert_after_model("ThemeIn")
    if idx is not None:
        return idx
    idx = find_insert_after_model("AskIn")
    if idx is not None:
        return idx
    for i, line in enumerate(lines):
        if line.startswith("@app."):
            return i
    return len(lines)

# 1) Ensure OverrideIn model exists in REAL python (not inside HTML_TEMPLATE)
pre_now = "".join(lines)
if not re.search(r"(?m)^class\s+OverrideIn\(BaseModel\)\s*:", pre_now):
    ins = find_insert_point()
    model_block = "\nclass OverrideIn(BaseModel):\n    topic: str\n    answer: str\n\n"
    lines.insert(ins, model_block)
    changed = True

# 2) Remove any REAL python /api/override handler if it exists (avoid duplicates)
new_lines = []
i = 0
while i < len(lines):
    if re.match(r'^@app\.post\(\s*[\'"]/api/override[\'"]\s*\)', lines[i]):
        i += 1
        while i < len(lines) and not lines[i].startswith("@app.") and not lines[i].startswith("HTML_TEMPLATE"):
            i += 1
        changed = True
        continue
    new_lines.append(lines[i])
    i += 1
lines = new_lines

pre_now = "".join(lines)

# 3) Add a REAL python POST /api/override route (self-contained writer)
if not re.search(r"(?m)^@app\.post\(\s*[\'\"]/api/override[\'\"]\s*\)", pre_now):
    route_block = r'''
@app.post("/api/override")
def api_override(payload: OverrideIn):
    """
    Save a corrected answer into local_knowledge.json (atomic write).
    Never crashes; always returns JSON.
    """
    import datetime as _dt
    import json as _json
    import os as _os
    import re as _re
    from pathlib import Path as _Path

    topic = (payload.topic or "").strip()
    answer = (payload.answer or "").strip()

    if not topic:
        return JSONResponse({"ok": False, "detail": "topic is required"}, status_code=422)
    if not answer:
        return JSONResponse({"ok": False, "detail": "answer is required"}, status_code=422)

    def norm(s: str) -> str:
        t = (s or "").strip()
        t = _re.sub(r"^\s*(what is|what's|define|explain)\s+", "", t, flags=_re.IGNORECASE).strip()
        t = _re.sub(r"[?!.]+$", "", t).strip()
        return t.lower()

    topic_n = norm(topic)
    if not topic_n:
        return JSONResponse({"ok": False, "detail": "topic normalized to empty"}, status_code=400)

    knowledge_path = _Path(_os.environ.get(
        "MS_KNOWLEDGE_PATH",
        str(REPO_DIR / "data" / "local_knowledge.json")
    )).resolve()

    # read db
    try:
        if knowledge_path.exists():
            raw = _json.loads(knowledge_path.read_text(encoding="utf-8", errors="replace") or "{}")
            db = raw if isinstance(raw, dict) else {}
        else:
            db = {}
    except Exception as e:
        db = {}

    ent = db.get(topic_n)
    if not isinstance(ent, dict):
        ent = {}

    ent["answer"] = answer
    ent["taught_by_user"] = True
    ent["notes"] = "override via UI (/api/override)"
    ent["updated"] = _dt.datetime.now().isoformat(timespec="seconds")

    try:
        old_c = float(ent.get("confidence", 0.0) or 0.0)
    except Exception:
        old_c = 0.0
    ent["confidence"] = max(old_c, 0.95)

    if not isinstance(ent.get("sources"), list):
        ent["sources"] = []

    db[topic_n] = ent

    # atomic write
    try:
        knowledge_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = knowledge_path.with_suffix(knowledge_path.suffix + ".tmp")
        tmp.write_text(_json.dumps(db, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        _os.replace(tmp, knowledge_path)
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        if len(tb) > 4000:
            tb = tb[-4000:]
        return JSONResponse({"ok": False, "detail": f"write failed: {type(e).__name__}: {e}", "trace": tb}, status_code=500)

    return {"ok": True, "topic": topic_n}

'''
    pre_now = pre_now.rstrip() + "\n\n" + route_block + "\n"
    changed = True

if not changed:
    print("OK: no changes needed (real /api/override already present)")
else:
    p.write_text(pre_now + post, encoding="utf-8")
    print("OK: patched ms_ui.py to add real POST /api/override")
