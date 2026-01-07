#!/usr/bin/env python3
# MachineSpirit brain.py
# NOTE: This file is intentionally self-contained (single file) and file-backed (JSON in ./data).
# It preserves the command set you listed and extends Phase 1 (queuehealth + retry/backoff + clearer headless logs).

import os
import re
import sys
import json
import time
import shutil
import signal
import difflib
import datetime
import threading
from typing import Dict, Any, List, Tuple, Optional

try:
    import urllib.parse
    import urllib.request
except Exception:
    urllib = None

APP_NAME = "Machine Spirit"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

KNOWLEDGE_PATH = os.path.join(DATA_DIR, "local_knowledge.json")
ALIASES_PATH = os.path.join(DATA_DIR, "aliases.json")

QUEUE_PATH = os.path.join(DATA_DIR, "research_queue.json")
PENDING_PATH = os.path.join(DATA_DIR, "pending_promotions.json")

LOGS_DIR = os.path.join(DATA_DIR, "logs")
EXPORTS_DIR = os.path.join(DATA_DIR, "exports")
BACKUPS_DIR = os.path.join(DATA_DIR, "backups")

WEBQUEUE_LOG = os.path.join(LOGS_DIR, "webqueue.log")
CURIOSITY_LOG = os.path.join(LOGS_DIR, "curiosity.log")
BRAIN_LOG = os.path.join(LOGS_DIR, "brain.log")

DEFAULT_MAX_QUEUE_ATTEMPTS = 3
DEFAULT_COOLDOWN_SECONDS = 6 * 60 * 60  # 6 hours

# -----------------------------
# Small utilities
# -----------------------------

def now_ts() -> int:
    return int(time.time())

def iso_now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")

def ensure_dirs() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(LOGS_DIR, exist_ok=True)
    os.makedirs(EXPORTS_DIR, exist_ok=True)
    os.makedirs(BACKUPS_DIR, exist_ok=True)

def atomic_write_json(path: str, obj: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

def read_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def safe_log(path: str, msg: str) -> None:
    try:
        ensure_dirs()
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{iso_now()}] {msg}\n")
    except Exception:
        pass

def backup_file(path: str) -> Optional[str]:
    if not os.path.exists(path):
        return None
    ensure_dirs()
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.basename(path)
    dst = os.path.join(BACKUPS_DIR, f"{base}.{ts}.bak")
    try:
        shutil.copy2(path, dst)
        return dst
    except Exception:
        return None

def normalize_topic(t: str) -> str:
    t = (t or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t

def is_urlish(s: str) -> bool:
    s = (s or "").strip()
    if not s:
        return False
    if re.search(r"^(https?://)", s, re.IGNORECASE):
        return True
    if re.search(r"\bwww\.", s, re.IGNORECASE):
        return True
    if re.search(r"\b[a-z0-9-]+\.[a-z]{2,}\b", s, re.IGNORECASE):
        # domain-ish (but keep it conservative)
        return True
    return False

def has_control_chars(s: str) -> bool:
    if s is None:
        return False
    for ch in s:
        o = ord(ch)
        if o < 32 and ch not in ("\n", "\r", "\t"):
            return True
    return False

def looks_like_terminal_command(s: str) -> bool:
    s = (s or "").strip()
    if not s:
        return False
    # Very common command starters / patterns that we saw polluting queue
    if s.startswith((">", "$", "#", "sudo ", "ssh ", "cd ", "ls", "cat ", "tail ", "grep ", "nano ", "rm ", "python", "./")):
        return True
    # Pipe / redirect heavy (likely shell)
    if (" | " in s) or (" >" in s) or ("< " in s) or (" && " in s) or (" || " in s):
        return True
    return False

def looks_like_transcript_prompt(s: str) -> bool:
    s = (s or "").strip().lower()
    if not s:
        return False
    # Examples: "copy and paste", "new chat handoff", "type a message", etc.
    bad = [
        "copy and paste",
        "new chat handoff",
        "type a message",
        "ctrl+c",
        "shutting down",
        "machine spirit brain online",
        "usage:",
        "open in gmail",
    ]
    return any(b in s for b in bad)

def is_junk_topic(s: str) -> Tuple[bool, str]:
    """
    Strict junk blocking for queue pollution.
    """
    if s is None:
        return True, "empty"
    s0 = s
    s = (s or "").strip()
    if not s:
        return True, "empty"
    if has_control_chars(s):
        return True, "control_chars"
    if len(s) > 200:
        return True, "too_long"
    if is_urlish(s):
        return True, "url_or_domain"
    if looks_like_terminal_command(s):
        return True, "terminal_command"
    if looks_like_transcript_prompt(s0):
        return True, "transcript_prompt"
    # avoid raw slash commands being queued as topics
    if s.startswith("/"):
        return True, "slash_command"
    return False, ""

def human_age(seconds: int) -> str:
    if seconds < 0:
        seconds = 0
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    if d > 0:
        return f"{d}d {h}h"
    if h > 0:
        return f"{h}h {m}m"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"

# -----------------------------
# Persistent stores
# -----------------------------

def load_knowledge() -> Dict[str, Any]:
    return read_json(KNOWLEDGE_PATH, {})

def save_knowledge(k: Dict[str, Any]) -> None:
    backup_file(KNOWLEDGE_PATH)
    atomic_write_json(KNOWLEDGE_PATH, k)

def load_aliases() -> Dict[str, str]:
    return read_json(ALIASES_PATH, {})

def save_aliases(a: Dict[str, str]) -> None:
    backup_file(ALIASES_PATH)
    atomic_write_json(ALIASES_PATH, a)

def load_queue() -> List[Dict[str, Any]]:
    q = read_json(QUEUE_PATH, [])
    if not isinstance(q, list):
        return []
    # Phase 1: ensure new fields exist (non-destructive)
    changed = False
    for item in q:
        if not isinstance(item, dict):
            continue
        if "max_attempts" not in item:
            item["max_attempts"] = DEFAULT_MAX_QUEUE_ATTEMPTS
            changed = True
        if "attempts" not in item:
            item["attempts"] = 0
            changed = True
        if "last_attempt_ts" not in item:
            item["last_attempt_ts"] = 0
            changed = True
        if "cooldown_seconds" not in item:
            item["cooldown_seconds"] = DEFAULT_COOLDOWN_SECONDS
            changed = True
        if "fail_reason" not in item:
            item["fail_reason"] = ""
            changed = True
        if "status" not in item:
            item["status"] = "pending"
            changed = True
    if changed:
        save_queue(q)
    return q

def save_queue(q: List[Dict[str, Any]]) -> None:
    backup_file(QUEUE_PATH)
    atomic_write_json(QUEUE_PATH, q)

def load_pending_promotions() -> List[Dict[str, Any]]:
    p = read_json(PENDING_PATH, [])
    if not isinstance(p, list):
        return []
    return p

def save_pending_promotions(p: List[Dict[str, Any]]) -> None:
    backup_file(PENDING_PATH)
    atomic_write_json(PENDING_PATH, p)

# -----------------------------
# Alias (fuzzy suggestion + accept)
# -----------------------------

def suggest_alias(topic: str, knowledge: Dict[str, Any], aliases: Dict[str, str]) -> Optional[str]:
    """
    Suggest best match in knowledge keys for topic.
    """
    t = normalize_topic(topic)
    if not t:
        return None

    # already alias?
    if t in aliases:
        return aliases[t]

    keys = list(knowledge.keys())
    if not keys:
        return None

    # strong exact-ish candidates
    if t in knowledge:
        return None

    # difflib best match
    match = difflib.get_close_matches(t, keys, n=1, cutoff=0.70)
    if match:
        return match[0]

    # substring fallback
    for k in keys:
        if t in k or k in t:
            return k
    return None

# -----------------------------
# Knowledge shape helpers
# -----------------------------

def ensure_entry_shape(entry: Dict[str, Any]) -> Dict[str, Any]:
    if "answer" not in entry:
        entry["answer"] = ""
    if "confidence" not in entry:
        entry["confidence"] = 0.5
    if "sources" not in entry:
        entry["sources"] = []
    if "notes" not in entry:
        entry["notes"] = ""
    if "updated" not in entry:
        entry["updated"] = iso_now()
    if "taught_by_user" not in entry:
        entry["taught_by_user"] = False
    return entry

def set_knowledge(topic: str, answer: str, confidence: float, sources: Optional[List[str]] = None,
                  notes: str = "", taught_by_user: bool = False) -> None:
    topic_n = normalize_topic(topic)
    if not topic_n:
        return
    k = load_knowledge()
    entry = ensure_entry_shape(k.get(topic_n, {}))
    entry["answer"] = (answer or "").strip()
    entry["confidence"] = float(confidence)
    entry["updated"] = iso_now()
    entry["notes"] = notes or entry.get("notes", "")
    entry["taught_by_user"] = bool(taught_by_user) or bool(entry.get("taught_by_user", False))
    if sources is not None:
        entry["sources"] = sources
    k[topic_n] = entry
    save_knowledge(k)

# -----------------------------
# Web fetch/search (fallback chain)
# -----------------------------

def http_get(url: str, timeout: int = 12, headers: Optional[Dict[str, str]] = None) -> Tuple[int, str]:
    if urllib is None:
        return 0, ""
    req = urllib.request.Request(url, headers=headers or {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = getattr(resp, "status", 200)
            body = resp.read()
            try:
                text = body.decode("utf-8", errors="replace")
            except Exception:
                text = str(body)
            return int(code), text
    except Exception:
        return 0, ""

def strip_html(html: str) -> str:
    if not html:
        return ""
    # Remove scripts/styles
    html = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    # Remove tags
    html = re.sub(r"(?is)<.*?>", " ", html)
    # Decode a few common entities (minimal)
    html = html.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    html = html.replace("&#39;", "'").replace("&quot;", '"')
    # Collapse whitespace
    html = re.sub(r"\s+", " ", html).strip()
    return html

def wiki_opensearch(query: str) -> Optional[Tuple[str, str]]:
    """
    Returns (title, snippet-ish text) from Wikipedia OpenSearch.
    """
    if urllib is None:
        return None
    q = urllib.parse.quote(query)
    url = f"https://en.wikipedia.org/w/api.php?action=opensearch&search={q}&limit=1&namespace=0&format=json"
    code, text = http_get(url)
    if code != 200 or not text:
        return None
    try:
        data = json.loads(text)
        # [searchterm, [titles], [descriptions], [links]]
        titles = data[1] if len(data) > 1 else []
        descs = data[2] if len(data) > 2 else []
        if titles:
            title = titles[0]
            desc = descs[0] if descs else ""
            return title, desc
    except Exception:
        return None
    return None

def ddg_html_search(query: str) -> Optional[Tuple[str, str]]:
    """
    DuckDuckGo HTML results (light parsing).
    """
    if urllib is None:
        return None
    q = urllib.parse.quote(query)
    url = f"https://duckduckgo.com/html/?q={q}"
    code, html = http_get(url)
    if code != 200 or not html:
        return None
    # first result link + snippet
    m = re.search(r'(?is)<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html)
    if not m:
        return None
    link = m.group(1)
    title = strip_html(m.group(2))
    # snippet
    sm = re.search(r'(?is)<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', html)
    snippet = strip_html(sm.group(1)) if sm else ""
    return f"{title} ({link})", snippet

def ddg_lite_search(query: str) -> Optional[Tuple[str, str]]:
    """
    DuckDuckGo lite results.
    """
    if urllib is None:
        return None
    q = urllib.parse.quote(query)
    url = f"https://lite.duckduckgo.com/lite/?q={q}"
    code, html = http_get(url)
    if code != 200 or not html:
        return None
    # crude: find first result link
    m = re.search(r'(?is)<a rel="nofollow" class="result-link" href="([^"]+)".*?>(.*?)</a>', html)
    if not m:
        # alternate pattern
        m = re.search(r'(?is)<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html)
    if not m:
        return None
    link = m.group(1)
    title = strip_html(m.group(2))
    return f"{title} ({link})", ""

def web_search_fallback(query: str) -> Tuple[bool, str, List[str]]:
    """
    Returns (ok, synthesized_short, sources)
    Phase 1 keeps this simple; Phase 2 will do structured synthesis.
    """
    sources: List[str] = []
    # 1) Wikipedia OpenSearch
    w = wiki_opensearch(query)
    if w:
        title, desc = w
        sources.append(f"Wikipedia OpenSearch: {title}")
        if desc:
            return True, f"{title}: {desc}", sources
        return True, f"{title}", sources

    # 2) DDG HTML
    d = ddg_html_search(query)
    if d:
        t, snip = d
        sources.append("DuckDuckGo HTML")
        if snip:
            return True, f"{t} — {snip}", sources
        return True, f"{t}", sources

    # 3) DDG Lite
    l = ddg_lite_search(query)
    if l:
        t, snip = l
        sources.append("DuckDuckGo Lite")
        if snip:
            return True, f"{t} — {snip}", sources
        return True, f"{t}", sources

    return False, "Web search returned no results or could not be fetched.", sources

# -----------------------------
# Queue logic (Phase 1: retry/backoff + health)
# -----------------------------

def queue_find_item(q: List[Dict[str, Any]], topic: str) -> Optional[Dict[str, Any]]:
    tn = normalize_topic(topic)
    for item in q:
        if normalize_topic(item.get("topic", "")) == tn:
            return item
    return None

def queue_add(topic: str, reason: str = "", confidence: float = 0.35) -> Tuple[bool, str]:
    topic_n = normalize_topic(topic)
    junk, why = is_junk_topic(topic_n)
    if junk:
        return False, f"Not queued (junk): {why}"
    q = load_queue()
    existing = queue_find_item(q, topic_n)
    if existing:
        # only bump reason if empty
        if reason and not existing.get("reason"):
            existing["reason"] = reason
            save_queue(q)
        return False, "Already queued."
    item = {
        "topic": topic_n,
        "reason": reason or "",
        "requested_on": iso_now(),
        "status": "pending",  # pending, running, done, failed, failed_final
        "current_confidence": float(confidence),

        # Phase 1 fields
        "attempts": 0,
        "max_attempts": DEFAULT_MAX_QUEUE_ATTEMPTS,
        "last_attempt_ts": 0,
        "cooldown_seconds": DEFAULT_COOLDOWN_SECONDS,
        "fail_reason": "",
        "completed_on": "",
        "worker_note": "",
    }
    q.append(item)
    save_queue(q)
    return True, "Queued."

def queue_clear_pending() -> int:
    q = load_queue()
    before = len(q)
    q2 = [i for i in q if i.get("status") != "pending"]
    save_queue(q2)
    return before - len(q2)

def queue_purge_junk_pending() -> int:
    q = load_queue()
    kept = []
    removed = 0
    for item in q:
        st = item.get("status", "pending")
        if st != "pending":
            kept.append(item)
            continue
        junk, _why = is_junk_topic(item.get("topic", ""))
        if junk:
            removed += 1
            continue
        kept.append(item)
    save_queue(kept)
    return removed

def queue_health_report() -> Dict[str, Any]:
    q = load_queue()
    counts = {"pending": 0, "running": 0, "done": 0, "failed": 0, "failed_final": 0, "other": 0}
    oldest_pending_ts: Optional[int] = None
    failure_reasons: Dict[str, int] = {}
    stuck_items: List[Dict[str, Any]] = []

    now = now_ts()
    for item in q:
        st = item.get("status", "pending")
        if st in counts:
            counts[st] += 1
        else:
            counts["other"] += 1

        if st == "pending":
            # requested_on is ISO; try to parse
            req = item.get("requested_on", "")
            ts = parse_iso_to_ts(req)
            if ts:
                if oldest_pending_ts is None or ts < oldest_pending_ts:
                    oldest_pending_ts = ts

        if st in ("failed", "failed_final"):
            r = (item.get("fail_reason") or "unknown").strip() or "unknown"
            failure_reasons[r] = failure_reasons.get(r, 0) + 1

        # Stuck detection
        if st == "running":
            last = int(item.get("last_attempt_ts") or 0)
            if last > 0 and (now - last) > (60 * 30):  # 30 min since last attempt marked running
                stuck_items.append(item)

        if st == "pending":
            # if it's pending but cooldown not satisfied and looks "stuck waiting"
            last = int(item.get("last_attempt_ts") or 0)
            cd = int(item.get("cooldown_seconds") or DEFAULT_COOLDOWN_SECONDS)
            if last > 0 and (now - last) < cd and int(item.get("attempts") or 0) > 0:
                # not truly stuck, but waiting
                pass

    oldest_pending_age = None
    if oldest_pending_ts is not None:
        oldest_pending_age = human_age(now - oldest_pending_ts)

    top_reasons = sorted(failure_reasons.items(), key=lambda x: x[1], reverse=True)[:5]
    return {
        "counts": counts,
        "oldest_pending_age": oldest_pending_age,
        "top_failure_reasons": top_reasons,
        "stuck_running_count": len(stuck_items),
        "stuck_running_items": [
            {
                "topic": i.get("topic", ""),
                "last_attempt_age": human_age(now - int(i.get("last_attempt_ts") or 0))
            } for i in stuck_items[:5]
        ],
    }

def parse_iso_to_ts(s: str) -> Optional[int]:
    s = (s or "").strip()
    if not s:
        return None
    try:
        # accept "YYYY-MM-DDTHH:MM:SS"
        dt = datetime.datetime.fromisoformat(s)
        return int(dt.timestamp())
    except Exception:
        return None

def can_attempt(item: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Retry/backoff gate:
    - skip if junk
    - skip if status not pending/failed
    - skip if attempts >= max_attempts -> failed_final
    - skip if cooldown not elapsed since last_attempt_ts
    """
    topic = item.get("topic", "")
    junk, why = is_junk_topic(topic)
    if junk:
        return False, f"junk:{why}"

    st = item.get("status", "pending")
    if st not in ("pending", "failed"):
        return False, f"status:{st}"

    attempts = int(item.get("attempts") or 0)
    max_attempts = int(item.get("max_attempts") or DEFAULT_MAX_QUEUE_ATTEMPTS)
    if attempts >= max_attempts:
        return False, "max_attempts_reached"

    last = int(item.get("last_attempt_ts") or 0)
    cd = int(item.get("cooldown_seconds") or DEFAULT_COOLDOWN_SECONDS)
    if last > 0:
        elapsed = now_ts() - last
        if elapsed < cd:
            return False, f"cooldown:{human_age(cd - elapsed)}"

    return True, "ok"

def mark_failed(item: Dict[str, Any], reason: str, final: bool = False) -> None:
    item["status"] = "failed_final" if final else "failed"
    item["fail_reason"] = (reason or "").strip()[:200]
    item["completed_on"] = iso_now() if final else item.get("completed_on", "")
    if final:
        item["worker_note"] = (item.get("worker_note") or "") + f" Finalized failure: {reason}"

def mark_done(item: Dict[str, Any], note: str = "") -> None:
    item["status"] = "done"
    item["completed_on"] = iso_now()
    if note:
        item["worker_note"] = note

def run_webqueue(limit: int = 3, autoupgrade: bool = True) -> Dict[str, Any]:
    """
    Headless safe runner for systemd timer use.
    """
    q = load_queue()
    attempted = 0
    learned = 0
    skipped = 0
    finalized = 0

    # work on earliest pending/failed first (stable order)
    # keep original order but filter by eligibility
    for item in q:
        if attempted >= limit:
            break

        ok, why = can_attempt(item)
        if not ok:
            # If junk: mark failed_final immediately (don’t keep retrying junk)
            if why.startswith("junk:"):
                mark_failed(item, why, final=True)
                safe_log(WEBQUEUE_LOG, f"webqueue: finalize junk topic='{item.get('topic','')}' reason='{why}'")
                finalized += 1
            elif why == "max_attempts_reached":
                mark_failed(item, "max_attempts_reached", final=True)
                safe_log(WEBQUEUE_LOG, f"webqueue: finalize max attempts topic='{item.get('topic','')}' attempts={item.get('attempts',0)}")
                finalized += 1
            else:
                skipped += 1
                safe_log(WEBQUEUE_LOG, f"webqueue: skip topic='{item.get('topic','')}' because {why}")
            continue

        # Attempt
        attempted += 1
        item["status"] = "running"
        item["attempts"] = int(item.get("attempts") or 0) + 1
        item["last_attempt_ts"] = now_ts()

        topic = item.get("topic", "")
        safe_log(WEBQUEUE_LOG, f"webqueue: attempt {item['attempts']}/{item.get('max_attempts',DEFAULT_MAX_QUEUE_ATTEMPTS)} topic='{topic}'")

        ok2, text, sources = web_search_fallback(topic)
        if not ok2:
            # transient failure -> failed (not final), will respect cooldown
            item["status"] = "failed"
            item["fail_reason"] = "web_fetch_failed"
            safe_log(WEBQUEUE_LOG, f"webqueue: failed topic='{topic}' reason='web_fetch_failed'")
            continue

        # If we got something back, store it as low-confidence learned note unless user already taught higher confidence.
        if autoupgrade:
            k = load_knowledge()
            existing = k.get(topic)
            existing_conf = float(existing.get("confidence", 0.0)) if isinstance(existing, dict) else 0.0
            taught_by_user = bool(existing.get("taught_by_user", False)) if isinstance(existing, dict) else False

            # Guardrail: do not overwrite strong user-taught answers
            if taught_by_user and existing_conf >= 0.75:
                mark_done(item, note="Skipped upgrade: user-taught answer is high confidence.")
                safe_log(WEBQUEUE_LOG, f"webqueue: done (skipped overwrite) topic='{topic}' taught_by_user=True conf={existing_conf}")
                continue

            # Write/update entry
            new_answer = text.strip()
            new_conf = max(float(item.get("current_confidence", 0.35)), 0.40)
            note = "Upgraded knowledge using web search fallback chain"
            set_knowledge(topic, new_answer, new_conf, sources=sources, notes=note, taught_by_user=False)
            learned += 1
            mark_done(item, note=f"{note}.")
            safe_log(WEBQUEUE_LOG, f"webqueue: learned topic='{topic}' sources={sources}")
        else:
            mark_done(item, note="Fetched (autoupgrade disabled).")
            safe_log(WEBQUEUE_LOG, f"webqueue: done topic='{topic}' autoupgrade=False")

    # finalize max attempts now (for items we didn’t touch this run)
    for item in q:
        if item.get("status") in ("pending", "failed"):
            attempts = int(item.get("attempts") or 0)
            max_attempts = int(item.get("max_attempts") or DEFAULT_MAX_QUEUE_ATTEMPTS)
            if attempts >= max_attempts:
                mark_failed(item, "max_attempts_reached", final=True)
                finalized += 1

    save_queue(q)
    safe_log(WEBQUEUE_LOG, f"run_webqueue: learned={learned} attempted={attempted} skipped={skipped} finalized={finalized} limit={limit}")
    return {"learned": learned, "attempted": attempted, "skipped": skipped, "finalized": finalized, "limit": limit}

# -----------------------------
# Promotions queue (/queue, /promote, /why)
# -----------------------------

def add_pending_promotion(topic: str, why: str = "") -> None:
    p = load_pending_promotions()
    topic_n = normalize_topic(topic)
    if not topic_n:
        return
    # dedupe
    for item in p:
        if normalize_topic(item.get("topic", "")) == topic_n:
            return
    p.append({"topic": topic_n, "why": why or "", "added": iso_now()})
    save_pending_promotions(p)

def pop_pending() -> Optional[Dict[str, Any]]:
    p = load_pending_promotions()
    if not p:
        return None
    item = p.pop(0)
    save_pending_promotions(p)
    return item

# -----------------------------
# Curiosity (minimal - existing command preserved)
# -----------------------------

def curiosity_tick(limit: int = 3) -> Dict[str, Any]:
    """
    Basic daily curiosity: look for lowest-confidence topics and queue them.
    Guardrails are already in place via junk checks and queue dedupe.
    """
    k = load_knowledge()
    # sort by confidence asc, then oldest updated
    items = []
    for topic, entry in k.items():
        if not isinstance(entry, dict):
            continue
        conf = float(entry.get("confidence", 0.0))
        upd = parse_iso_to_ts(entry.get("updated", "")) or 0
        items.append((conf, upd, topic))
    items.sort(key=lambda x: (x[0], x[1]))

    queued = 0
    considered = 0
    for conf, _upd, topic in items:
        if queued >= limit:
            break
        considered += 1
        ok, msg = queue_add(topic, reason="Curiosity: low confidence topic", confidence=max(conf, 0.35))
        if ok:
            queued += 1

    safe_log(CURIOSITY_LOG, f"curiosity: considered={considered} queued={queued} limit={limit}")
    return {"considered": considered, "queued": queued, "limit": limit}

# -----------------------------
# Command parsing helpers
# -----------------------------

def split_pipe(cmd: str) -> Tuple[str, str]:
    """
    Split "left | right" once.
    """
    if "|" not in cmd:
        return cmd.strip(), ""
    left, right = cmd.split("|", 1)
    return left.strip(), right.strip()

def print_help() -> None:
    print(f"""{APP_NAME} commands:

/teach <topic> | <answer>
/teachfile <topic> | <path>
/import <path>
/importfolder <folder>
/ingest <topic> | <text>
/export
/queue
/clearpending
/purgejunk
/promote
/confidence <topic>
/lowest [n]
/alias <from> | <to>
/aliases
/unalias <from>
/why <topic>
/accept
/suggest
/weblearn <topic>
/webqueue [limit]
/queuehealth
/curiosity [limit]

Type a normal topic name (example: "subnetting") to get an answer.
""")

# -----------------------------
# Core interaction
# -----------------------------

def resolve_topic(topic: str, aliases: Dict[str, str]) -> str:
    t = normalize_topic(topic)
    if t in aliases:
        return normalize_topic(aliases[t])
    return t

def get_answer_for_topic(topic: str) -> Optional[Dict[str, Any]]:
    k = load_knowledge()
    topic_n = normalize_topic(topic)
    entry = k.get(topic_n)
    if isinstance(entry, dict):
        return ensure_entry_shape(entry)
    return None

def show_topic(topic: str) -> None:
    aliases = load_aliases()
    k = load_knowledge()

    topic_raw = topic
    topic_n = normalize_topic(topic_raw)
    resolved = resolve_topic(topic_n, aliases)

    # If alias suggestion exists, show it (but don't force)
    if resolved == topic_n:
        sug = suggest_alias(topic_n, k, aliases)
        if sug and sug != topic_n:
            print(f"Suggestion: /alias {topic_n} | {sug}")

    entry = k.get(resolved)
    if isinstance(entry, dict) and entry.get("answer"):
        entry = ensure_entry_shape(entry)
        print(entry["answer"])
        # If low confidence, queue it
        if float(entry.get("confidence", 0.0)) < 0.60:
            ok, _ = queue_add(resolved, reason="Answer exists but confidence is low", confidence=float(entry["confidence"]))
            if ok:
                safe_log(BRAIN_LOG, f"autoqueue: topic='{resolved}' reason='low_confidence'")
        return

    # no answer
    print("Machine Spirit: I do not have a taught answer for that yet. If my reply is wrong or weak, correct me in your own words and I will remember it. My analysis may be incomplete. If this seems wrong, correct me and I will update my understanding. I have also marked this topic for deeper research so I can improve my answer over time.")
    queue_add(topic_n, reason="No taught answer yet", confidence=0.35)

def cmd_teach(arg: str) -> None:
    left, right = split_pipe(arg)
    topic = normalize_topic(left.replace("/teach", "", 1).strip())
    answer = right
    if not topic or not answer:
        print("Usage: /teach <topic> | <answer>")
        return
    set_knowledge(topic, answer, confidence=0.90, sources=[], notes="Updated by user re teach", taught_by_user=True)
    print("Saved.")

def cmd_teachfile(arg: str) -> None:
    left, right = split_pipe(arg)
    topic = normalize_topic(left.replace("/teachfile", "", 1).strip())
    path = right
    if not topic or not path:
        print("Usage: /teachfile <topic> | <path>")
        return
    if not os.path.exists(path):
        print("File not found.")
        return
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception:
        print("Could not read file.")
        return
    set_knowledge(topic, text, confidence=0.90, sources=[f"local file: {path}"], notes="Taught via teachfile", taught_by_user=True)
    print("Saved.")

def cmd_ingest(arg: str) -> None:
    left, right = split_pipe(arg)
    topic = normalize_topic(left.replace("/ingest", "", 1).strip())
    text = right
    if not topic or not text:
        print("Usage: /ingest <topic> | <text>")
        return
    set_knowledge(topic, text, confidence=0.75, sources=[], notes="Ingested text", taught_by_user=True)
    print("Saved.")

def cmd_import(path: str) -> None:
    path = path.replace("/import", "", 1).strip()
    if not path:
        print("Usage: /import <path>")
        return
    if not os.path.exists(path):
        print("File not found.")
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        print("Import failed (not valid JSON).")
        return
    if not isinstance(data, dict):
        print("Import failed (expected JSON object).")
        return
    k = load_knowledge()
    merged = 0
    for topic, entry in data.items():
        t = normalize_topic(topic)
        if not t:
            continue
        if isinstance(entry, dict):
            k[t] = ensure_entry_shape(entry)
        else:
            k[t] = ensure_entry_shape({"answer": str(entry), "confidence": 0.5})
        merged += 1
    save_knowledge(k)
    print(f"Imported {merged} entries.")

def cmd_importfolder(folder: str) -> None:
    folder = folder.replace("/importfolder", "", 1).strip()
    if not folder:
        print("Usage: /importfolder <folder>")
        return
    if not os.path.isdir(folder):
        print("Folder not found.")
        return
    files = [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(".json")]
    files.sort()
    count = 0
    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                k = load_knowledge()
                for topic, entry in data.items():
                    t = normalize_topic(topic)
                    if not t:
                        continue
                    if isinstance(entry, dict):
                        k[t] = ensure_entry_shape(entry)
                    else:
                        k[t] = ensure_entry_shape({"answer": str(entry), "confidence": 0.5})
                save_knowledge(k)
                count += 1
        except Exception:
            continue
    print(f"Imported {count} JSON files.")

def cmd_export() -> None:
    k = load_knowledge()
    ensure_dirs()
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(EXPORTS_DIR, f"knowledge_export_{ts}.json")
    atomic_write_json(out, k)
    print(f"Exported to {out}")

def cmd_queue() -> None:
    q = load_queue()
    if not q:
        print("Queue is empty.")
        return
    # Show latest 15
    print("Research queue (latest 15):")
    for item in q[-15:]:
        print(f"- {item.get('topic','')} | {item.get('status','')} | attempts={item.get('attempts',0)}/{item.get('max_attempts',DEFAULT_MAX_QUEUE_ATTEMPTS)} | reason={item.get('reason','')}")

def cmd_clearpending() -> None:
    removed = queue_clear_pending()
    print(f"Removed {removed} pending items.")

def cmd_purgejunk() -> None:
    removed = queue_purge_junk_pending()
    print(f"Purged {removed} junk pending items.")

def cmd_queuehealth() -> None:
    rep = queue_health_report()
    c = rep["counts"]
    print("Queue health:")
    print(f"- pending: {c.get('pending',0)}")
    print(f"- running: {c.get('running',0)}")
    print(f"- done: {c.get('done',0)}")
    print(f"- failed: {c.get('failed',0)}")
    print(f"- failed_final: {c.get('failed_final',0)}")
    if rep.get("oldest_pending_age"):
        print(f"- oldest pending age: {rep['oldest_pending_age']}")
    top = rep.get("top_failure_reasons", [])
    if top:
        print("- top failure reasons:")
        for reason, n in top:
            print(f"  - {reason}: {n}")
    if rep.get("stuck_running_count", 0) > 0:
        print(f"- stuck running items: {rep['stuck_running_count']}")
        for it in rep.get("stuck_running_items", []):
            print(f"  - {it.get('topic','')} (last attempt {it.get('last_attempt_age','')})")

def cmd_confidence(arg: str) -> None:
    topic = normalize_topic(arg.replace("/confidence", "", 1).strip())
    if not topic:
        print("Usage: /confidence <topic>")
        return
    aliases = load_aliases()
    resolved = resolve_topic(topic, aliases)
    entry = get_answer_for_topic(resolved)
    if not entry:
        print("No entry yet for that topic. Teach it first.")
        return
    print(f"{resolved} confidence: {entry.get('confidence',0.0)} (updated {entry.get('updated','')})")

def cmd_lowest(arg: str) -> None:
    n_str = arg.replace("/lowest", "", 1).strip()
    n = 10
    if n_str:
        try:
            n = int(n_str)
        except Exception:
            n = 10
    k = load_knowledge()
    items = []
    for topic, entry in k.items():
        if not isinstance(entry, dict):
            continue
        conf = float(entry.get("confidence", 0.0))
        items.append((conf, topic))
    items.sort(key=lambda x: x[0])
    print(f"Lowest confidence (top {n}):")
    for conf, topic in items[:n]:
        print(f"- {topic}: {conf}")

def cmd_alias(arg: str) -> None:
    left, right = split_pipe(arg)
    frm = normalize_topic(left.replace("/alias", "", 1).strip())
    to = normalize_topic(right)
    if not frm or not to:
        print("Usage: /alias <from> | <to>")
        return
    a = load_aliases()
    a[frm] = to
    save_aliases(a)
    print("Saved alias.")

def cmd_aliases() -> None:
    a = load_aliases()
    if not a:
        print("No aliases.")
        return
    print("Aliases:")
    for k, v in sorted(a.items()):
        print(f"- {k} -> {v}")

def cmd_unalias(arg: str) -> None:
    frm = normalize_topic(arg.replace("/unalias", "", 1).strip())
    if not frm:
        print("Usage: /unalias <from>")
        return
    a = load_aliases()
    if frm in a:
        del a[frm]
        save_aliases(a)
        print("Removed.")
    else:
        print("No such alias.")

def cmd_suggest() -> None:
    """
    Suggest an alias for the last typed topic is hard without state.
    This keeps the command for compatibility: it suggests based on the last pending queue item, if any.
    """
    q = load_queue()
    k = load_knowledge()
    a = load_aliases()

    # pick last pending item topic
    pending = [i for i in q if i.get("status") == "pending"]
    if not pending:
        print("No pending topics to suggest for.")
        return
    topic = pending[-1].get("topic", "")
    sug = suggest_alias(topic, k, a)
    if sug and sug != topic:
        print(f"Suggestion: /alias {topic} | {sug}")
    else:
        print("No good suggestion found.")

def cmd_accept() -> None:
    """
    Accept the best alias suggestion for the last pending item.
    """
    q = load_queue()
    k = load_knowledge()
    a = load_aliases()

    pending = [i for i in q if i.get("status") == "pending"]
    if not pending:
        print("No pending topics to accept for.")
        return
    topic = pending[-1].get("topic", "")
    sug = suggest_alias(topic, k, a)
    if not sug or sug == topic:
        print("No suggestion available.")
        return
    a[topic] = sug
    save_aliases(a)
    print(f"Accepted: {topic} -> {sug}")

def cmd_why(arg: str) -> None:
    topic = normalize_topic(arg.replace("/why", "", 1).strip())
    if not topic:
        print("Usage: /why <topic>")
        return
    q = load_queue()
    for item in q:
        if normalize_topic(item.get("topic", "")) == topic:
            print(f"Why queued: {item.get('reason','')}")
            print(f"Status: {item.get('status','')}")
            print(f"Attempts: {item.get('attempts',0)}/{item.get('max_attempts',DEFAULT_MAX_QUEUE_ATTEMPTS)}")
            if item.get("fail_reason"):
                print(f"Fail reason: {item.get('fail_reason')}")
            return
    print("Not found in queue.")

def cmd_promote() -> None:
    """
    Promote one pending learned item into an explicit 'pending promotions' list.
    Keeps command for compatibility.
    """
    q = load_queue()
    # find a "done" item that was learned, push to pending promotions
    done = [i for i in q if i.get("status") == "done"]
    if not done:
        print("Nothing to promote.")
        return
    item = done[-1]
    add_pending_promotion(item.get("topic", ""), why=item.get("worker_note", ""))
    print("Added to pending promotions.")

def cmd_weblearn(arg: str) -> None:
    topic = normalize_topic(arg.replace("/weblearn", "", 1).strip())
    if not topic:
        print("Usage: /weblearn <topic>")
        return
    junk, why = is_junk_topic(topic)
    if junk:
        print(f"Refusing (junk topic): {why}")
        return
    ok, text, sources = web_search_fallback(topic)
    if not ok:
        print(text)
        return
    # store low confidence
    set_knowledge(topic, text.strip(), confidence=0.45, sources=sources, notes="Learned via /weblearn", taught_by_user=False)
    print("Learned.")
    # also queue for deeper follow-up if still low confidence
    queue_add(topic, reason="Learned via /weblearn (needs synthesis later)", confidence=0.45)

def cmd_webqueue(arg: str) -> None:
    rest = arg.replace("/webqueue", "", 1).strip()
    limit = 3
    if rest:
        try:
            limit = int(rest)
        except Exception:
            limit = 3
    res = run_webqueue(limit=limit, autoupgrade=True)
    print(f"Web queue run complete. Learned {res['learned']} out of {res['attempted']} attempted (limit {res['limit']}).")

def cmd_curiosity(arg: str) -> None:
    rest = arg.replace("/curiosity", "", 1).strip()
    limit = 3
    if rest:
        try:
            limit = int(rest)
        except Exception:
            limit = 3
    res = curiosity_tick(limit=limit)
    print(f"Curiosity complete. Queued {res['queued']} out of {res['considered']} considered (limit {res['limit']}).")

# -----------------------------
# Main loop
# -----------------------------

STOP = False

def handle_sigint(_sig, _frame):
    global STOP
    STOP = True

def main() -> None:
    ensure_dirs()
    signal.signal(signal.SIGINT, handle_sigint)

    # ensure base files exist
    if not os.path.exists(KNOWLEDGE_PATH):
        atomic_write_json(KNOWLEDGE_PATH, {})
    if not os.path.exists(ALIASES_PATH):
        atomic_write_json(ALIASES_PATH, {})
    if not os.path.exists(QUEUE_PATH):
        atomic_write_json(QUEUE_PATH, [])
    if not os.path.exists(PENDING_PATH):
        atomic_write_json(PENDING_PATH, [])

    print(f"{APP_NAME} brain online. Type a message, or /help for commands. Ctrl+C to exit.")

    while not STOP:
        try:
            user = input("> ").strip()
        except EOFError:
            break
        except KeyboardInterrupt:
            break

        if not user:
            continue

        # reject raw URL inputs (existing behavior)
        if is_urlish(user) and not user.startswith("/"):
            print("Machine Spirit: That looks like a URL or domain string. Ask using a normal topic name instead (example: 'rfc 1918').")
            continue

        # commands
        if user.startswith("/"):
            if user in ("/help", "/h", "/?"):
                print_help()
                continue

            if user.startswith("/teach "):
                cmd_teach(user)
                continue
            if user.startswith("/teachfile "):
                cmd_teachfile(user)
                continue
            if user.startswith("/ingest "):
                cmd_ingest(user)
                continue
            if user.startswith("/importfolder"):
                cmd_importfolder(user)
                continue
            if user.startswith("/import"):
                cmd_import(user)
                continue
            if user.startswith("/export"):
                cmd_export()
                continue
            if user.startswith("/queuehealth"):
                cmd_queuehealth()
                continue
            if user.startswith("/queue"):
                cmd_queue()
                continue
            if user.startswith("/clearpending"):
                cmd_clearpending()
                continue
            if user.startswith("/purgejunk"):
                cmd_purgejunk()
                continue
            if user.startswith("/promote"):
                cmd_promote()
                continue
            if user.startswith("/confidence"):
                cmd_confidence(user)
                continue
            if user.startswith("/lowest"):
                cmd_lowest(user)
                continue
            if user.startswith("/alias "):
                cmd_alias(user)
                continue
            if user.startswith("/aliases"):
                cmd_aliases()
                continue
            if user.startswith("/unalias"):
                cmd_unalias(user)
                continue
            if user.startswith("/why"):
                cmd_why(user)
                continue
            if user.startswith("/accept"):
                cmd_accept()
                continue
            if user.startswith("/suggest"):
                cmd_suggest()
                continue
            if user.startswith("/weblearn"):
                cmd_weblearn(user)
                continue
            if user.startswith("/webqueue"):
                cmd_webqueue(user)
                continue
            if user.startswith("/curiosity"):
                cmd_curiosity(user)
                continue

            print("Unknown command. Type /help.")
            continue

        # normal topic
        show_topic(user)

    print("Shutting down.")

if __name__ == "__main__":
    main()
