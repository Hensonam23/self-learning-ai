#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_DIR / "data"
LOGS_DIR = DATA_DIR / "logs"
REFL_DIR = DATA_DIR / "reflections"

KNOWLEDGE_PATH = Path(os.environ.get("MS_KNOWLEDGE_PATH", str(DATA_DIR / "local_knowledge.json"))).resolve()
QUEUE_PATH = DATA_DIR / "research_queue.json"
AUTONOMY_PATH = DATA_DIR / "autonomy.json"

def read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8", errors="replace") or "")
    except Exception:
        return default

def tail_lines(path: Path, n: int = 120) -> List[str]:
    try:
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-n:]
    except Exception:
        return []

def iso_now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")

def classify_pinned(ent: Dict[str, Any]) -> bool:
    try:
        if ent.get("taught_by_user") is True:
            return True
        c = float(ent.get("confidence", 0.0) or 0.0)
        return c >= 0.90
    except Exception:
        return False

def summarize_knowledge(db: Dict[str, Any]) -> Dict[str, Any]:
    entries = 0
    pinned = 0
    taught = 0
    confs: List[float] = []
    lowest: List[tuple] = []

    for k, v in db.items():
        if not isinstance(v, dict):
            continue
        entries += 1
        if v.get("taught_by_user") is True:
            taught += 1
        try:
            c = float(v.get("confidence", 0.0) or 0.0)
            confs.append(c)
            lowest.append((c, k))
        except Exception:
            pass
        if classify_pinned(v):
            pinned += 1

    avg_conf = (sum(confs) / len(confs)) if confs else 0.0
    lowest.sort(key=lambda x: x[0])
    lowest_10 = [{"topic": t, "confidence": round(c, 3)} for c, t in lowest[:10]]

    return {
        "entries": entries,
        "pinned": pinned,
        "taught_by_user": taught,
        "avg_confidence": round(avg_conf, 3),
        "lowest_10": lowest_10,
    }

def summarize_queue(q: Any) -> Dict[str, Any]:
    arr = q if isinstance(q, list) else []
    counts: Dict[str, int] = {}
    pending: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    done: List[Dict[str, Any]] = []

    for it in arr:
        if not isinstance(it, dict):
            continue
        st = str(it.get("status", "unknown"))
        counts[st] = counts.get(st, 0) + 1
        if st == "pending":
            pending.append(it)
        elif st.startswith("fail"):
            failed.append(it)
        elif st == "done":
            done.append(it)

    # recent done by completed_on if present else requested_on
    def when(it: Dict[str, Any]) -> str:
        return str(it.get("completed_on") or it.get("requested_on") or "")

    done_sorted = sorted(done, key=when, reverse=True)
    pending_sorted = sorted(pending, key=lambda x: str(x.get("requested_on") or ""), reverse=False)

    return {
        "counts": counts,
        "pending_top": [{"topic": x.get("topic",""), "reason": x.get("reason","")} for x in pending_sorted[:10]],
        "failed_top": [{"topic": x.get("topic",""), "reason": x.get("reason",""), "status": x.get("status","")} for x in failed[:10]],
        "recent_done": [{"topic": x.get("topic",""), "note": x.get("worker_note","")} for x in done_sorted[:10]],
        "total": len(arr),
    }

def recommend(knowledge: Dict[str, Any], queue: Dict[str, Any], logs: Dict[str, Any]) -> List[str]:
    rec: List[str] = []
    pending_n = int(queue.get("counts", {}).get("pending", 0) or 0)
    failed_n = 0
    for k, v in (queue.get("counts", {}) or {}).items():
        if str(k).startswith("fail"):
            failed_n += int(v or 0)

    avgc = float(knowledge.get("avg_confidence", 0.0) or 0.0)

    if pending_n > 0:
        rec.append(f"Queue has {pending_n} pending items: propose a research run (curiosity n=10).")

    if failed_n > 0:
        rec.append(f"Queue has {failed_n} failed items: propose investigating failing sources/topics.")

    if avgc < 0.55 and knowledge.get("entries", 0) > 0:
        rec.append("Average confidence is low: propose a focused learning run (curiosity n=15) and review lowest_10 topics.")

    # if logs show errors
    err_hits = int(logs.get("error_lines", 0) or 0)
    if err_hits > 0:
        rec.append(f"Recent logs include {err_hits} error-like lines: propose a quick health audit.")

    if not rec:
        rec.append("System looks stable: propose a small maintenance research run (curiosity n=5).")

    return rec

def main() -> int:
    REFL_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    now = dt.datetime.now().astimezone()
    ymd = now.strftime("%Y-%m-%d")
    out_json = REFL_DIR / f"{ymd}.json"
    out_md = REFL_DIR / f"{ymd}.md"

    db_raw = read_json(KNOWLEDGE_PATH, {})
    db = db_raw if isinstance(db_raw, dict) else {}

    q_raw = read_json(QUEUE_PATH, [])
    a_raw = read_json(AUTONOMY_PATH, {})

    knowledge = summarize_knowledge(db)
    queue = summarize_queue(q_raw)
    autonomy = a_raw if isinstance(a_raw, dict) else {}

    # scan a few known log files for quick “error vibe”
    log_files = [
        LOGS_DIR / "webqueue.log",
        LOGS_DIR / "curiosity.log",
        LOGS_DIR / "night_learner.log",
    ]
    log_tail: Dict[str, List[str]] = {}
    err_lines = 0
    for lf in log_files:
        t = tail_lines(lf, n=120)
        log_tail[lf.name] = t
        for line in t:
            s = line.lower()
            if "error" in s or "traceback" in s or "exception" in s or "failed" in s:
                err_lines += 1

    logs = {"tail": log_tail, "error_lines": err_lines}

    recs = recommend(knowledge, queue, logs)

    payload = {
        "ok": True,
        "created_at": iso_now(),
        "date": ymd,
        "knowledge": knowledge,
        "research_queue": queue,
        "autonomy": autonomy,
        "logs": {"error_lines": err_lines},
        "recommendations": recs,
    }

    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # markdown summary (human readable)
    md = []
    md.append(f"# MachineSpirit Reflection — {ymd}")
    md.append("")
    md.append(f"- Created: {payload['created_at']}")
    md.append("")
    md.append("## Knowledge")
    md.append(f"- Entries: {knowledge['entries']}")
    md.append(f"- Pinned: {knowledge['pinned']} (taught_by_user: {knowledge['taught_by_user']})")
    md.append(f"- Avg confidence: {knowledge['avg_confidence']}")
    md.append("")
    md.append("### Lowest 10 topics")
    for x in knowledge["lowest_10"]:
        md.append(f"- {x['topic']}: {x['confidence']}")
    md.append("")
    md.append("## Research queue")
    md.append(f"- Total: {queue['total']}")
    md.append(f"- Counts: {json.dumps(queue['counts'], ensure_ascii=False)}")
    md.append("")
    md.append("### Pending (top)")
    for x in queue["pending_top"]:
        md.append(f"- {x.get('topic','')}: {x.get('reason','')}")
    md.append("")
    md.append("### Failed (top)")
    for x in queue["failed_top"]:
        md.append(f"- {x.get('topic','')}: {x.get('status','')} — {x.get('reason','')}")
    md.append("")
    md.append("## Recommendations")
    for r in recs:
        md.append(f"- {r}")
    md.append("")
    out_md.write_text("\n".join(md) + "\n", encoding="utf-8")

    # small log line for debug
    (LOGS_DIR / "reflect.log").write_text(f"[{payload['created_at']}] wrote {out_json.name}\n", encoding="utf-8")

    print(f"OK: wrote reflection: {out_json}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
