#!/usr/bin/env python3
# MachineSpirit - brain.py (single-file, self-contained)

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
    import urllib.error
except Exception:
    urllib = None


APP_NAME = "Machine Spirit"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

KNOWLEDGE_PATH = os.path.join(DATA_DIR, "local_knowledge.json")
ALIASES_PATH = os.path.join(DATA_DIR, "aliases.json")
PENDING_PATH = os.path.join(DATA_DIR, "pending_fixes.json")
RESEARCH_QUEUE_PATH = os.path.join(DATA_DIR, "research_queue.json")

RESEARCH_NOTES_DIR = os.path.join(DATA_DIR, "research_notes")
LOG_DIR = os.path.join(DATA_DIR, "logs")
EXPORTS_DIR = os.path.join(DATA_DIR, "exports")
BACKUPS_DIR = os.path.join(DATA_DIR, "backups")

CURIOSITY_LOG = os.path.join(LOG_DIR, "curiosity.log")
WEBQUEUE_LOG = os.path.join(LOG_DIR, "webqueue.log")

# Web controls
DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
HTTP_TIMEOUT = 12
SEARCH_TIMEOUT = 10
MAX_SEARCH_RESULTS = 6
MAX_FETCH_PAGES = 3
MAX_PAGE_BYTES = 900_000
MIN_TEXT_CHARS = 600
MAX_NOTE_CHARS = 20_000

# Queue controls
WEBQUEUE_DEFAULT_LIMIT = 3
MIN_CONFIDENCE_FOR_NO_QUEUE = 0.70

# Fuzzy suggestion controls
FUZZY_CUTOFF = 0.72
FUZZY_SUGGEST_N = 3

# Hard ignore obvious junk topics so they never get queued
IGNORE_QUEUE_TOPICS = {
    "test topic",
    "test_topic",
    "testing",
}

# Internal state
_shutdown = False
_last_suggestion: Optional[Dict[str, str]] = None  # {"alias": "...", "target": "...", "reason": "..."}
_lock = threading.Lock()


# ----------------------------
# Utilities
# ----------------------------

def now_iso() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d")


def ensure_dirs() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(RESEARCH_NOTES_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(EXPORTS_DIR, exist_ok=True)
    os.makedirs(BACKUPS_DIR, exist_ok=True)


def log_line(path: str, msg: str) -> None:
    try:
        ensure_dirs()
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{now_iso()}] {msg}\n")
    except Exception:
        pass


def load_json(path: str, default):
    try:
        if not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, data) -> None:
    ensure_dirs()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def safe_slug(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^a-z0-9_]+", "", s)
    if not s:
        s = "topic"
    return s[:120]


def strip_prompt_prefixes(s: str) -> str:
    """
    Strips transcript prompts like:
      '> thing' or '>> thing' or '> > thing'
    """
    t = s.strip()
    while True:
        new = re.sub(r"^\s*(?:>\s*)+", "", t)
        if new == t:
            break
        t = new.strip()
    return t


def strip_control_chars(s: str) -> str:
    """
    Removes ASCII control characters (including ESC) that show up when users paste
    terminal artifacts like arrow keys, etc.
    """
    # keep newlines/tabs? For topics, we only want single-line anyway.
    s = s.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return re.sub(r"[\x00-\x1f\x7f]", "", s)


def normalize_topic(s: str) -> str:
    s = strip_prompt_prefixes(s)
    s = strip_control_chars(s)
    s = s.strip()
    s = re.sub(r"\s+", " ", s)
    s = s.strip("\"'`")
    return s


def looks_like_url_or_domain(s: str) -> bool:
    t = s.strip().lower()

    if re.match(r"^(https?://|ftp://)", t):
        return True
    if t.startswith("www."):
        return True
    if "/" in t and "." in t.split("/")[0]:
        return True
    if re.match(r"^[a-z0-9-]+\.(com|net|org|io|edu|gov|mil|co|uk|de|jp|fr|ru|cn|info|biz)(/.*)?$", t):
        return True
    if re.match(r"^\d{1,3}(\.\d{1,3}){3}(:\d+)?(/.*)?$", t):
        return True

    return False


def pretty_conf(x: float) -> str:
    try:
        return f"{float(x):.2f}"
    except Exception:
        return "0.00"


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# -------------------------
# Terminal command detection
# -------------------------

def is_probably_terminal_command(text: str) -> bool:
    """
    If the user types shell commands inside the brain prompt, we must NOT treat
    it as a topic, alias, or queue item.
    """
    t = normalize_topic(text)
    if not t:
        return False

    if t.endswith("?"):
        return False

    lower = t.lower()

    # If it's clearly a "question sentence", not a command
    for qword in ("what is", "what does", "how do", "how to", "why does", "explain", "help me"):
        if lower.startswith(qword):
            return False

    # typical shell operators
    if any(ch in t for ch in ["|", "&&", "||", ";", ">", "<", "$(", "`"]):
        return True

    # prompt-looking
    if re.search(r"^[\w-]+@[\w-]+:.*\$\s+", t):
        return True

    # sudo
    if lower.startswith("sudo "):
        rest = lower[5:].lstrip()
        if not rest:
            return False
        lower = rest
        t = rest

    common_cmds = {
        "cd", "ls", "pwd", "whoami", "clear",
        "git", "nano", "vim", "vi",
        "python", "python3", "pip", "pip3",
        "docker", "docker-compose", "compose",
        "apt", "apt-get", "dnf", "yum", "pacman",
        "systemctl", "journalctl", "service",
        "rm", "cp", "mv", "cat", "less", "more", "head", "tail",
        "grep", "find", "chmod", "chown",
        "mkdir", "rmdir", "touch",
        "curl", "wget", "ping", "ip", "ifconfig", "ss", "netstat",
        "ssh", "scp",
        "make", "npm", "node", "yarn",
        "tar", "zip", "unzip",
    }
    parts = lower.split()
    first = parts[0] if parts else ""
    if first in common_cmds:
        return True

    if t.startswith("./") or t.startswith("/") or re.match(r"^[A-Za-z]:\\", t):
        return True

    # looks like options flags in a command
    if re.search(r"\s-\w", t) and (" " in t) and not re.search(r"[.!?]$", t):
        return True

    return False


def is_garbage_topic(s: str) -> Tuple[bool, str]:
    """
    Central gatekeeper: anything that fails here must never be queued,
    and webqueue must skip it even if already present in JSON.
    """
    t = normalize_topic(s)
    if not t:
        return True, "empty topic"

    low = t.strip().lower()

    if low in IGNORE_QUEUE_TOPICS:
        return True, "ignored test topic"

    # Control chars already stripped, but if it's still weird short, block.
    if len(low) < 2:
        return True, "too short"
    if len(low) > 140:
        return True, "too long"

    # commands / transcripts
    if low.startswith("/"):
        return True, "looks like a command"
    if low.startswith(">"):
        return True, "looks like transcript prompt"
    if is_probably_terminal_command(low):
        return True, "looks like a terminal command"

    # URLs/domains
    if looks_like_url_or_domain(low):
        return True, "looks like a URL/domain"

    # mostly symbols
    if re.match(r"^[^\w]+$", low):
        return True, "only symbols"

    # too many odd characters (after normalization)
    weird = re.findall(r"[^\w\s\-\.\(\):]", low)
    if len(weird) > 6:
        return True, "too many unusual characters"

    return False, ""


# ----------------------------
# Storage
# ----------------------------

def load_knowledge() -> Dict[str, Any]:
    return load_json(KNOWLEDGE_PATH, {})


def save_knowledge(k: Dict[str, Any]) -> None:
    save_json(KNOWLEDGE_PATH, k)


def load_aliases() -> Dict[str, str]:
    return load_json(ALIASES_PATH, {})


def save_aliases(a: Dict[str, str]) -> None:
    save_json(ALIASES_PATH, a)


def load_pending() -> List[Dict[str, Any]]:
    return load_json(PENDING_PATH, [])


def save_pending(p: List[Dict[str, Any]]) -> None:
    save_json(PENDING_PATH, p)


def load_research_queue() -> List[Dict[str, Any]]:
    data = load_json(RESEARCH_QUEUE_PATH, [])
    # normalize any existing queue topics in-place (soft cleanup)
    changed = False
    if isinstance(data, list):
        for it in data:
            if not isinstance(it, dict):
                continue
            raw = str(it.get("topic", "") or "")
            norm = normalize_topic(raw).lower()
            if norm != raw:
                it["topic"] = norm
                changed = True
    if changed:
        save_json(RESEARCH_QUEUE_PATH, data)
    return data if isinstance(data, list) else []


def save_research_queue(q: List[Dict[str, Any]]) -> None:
    save_json(RESEARCH_QUEUE_PATH, q)


def resolve_alias(topic: str, aliases: Dict[str, str]) -> str:
    t = topic.strip().lower()
    if t in aliases:
        return aliases[t]
    return topic


# ----------------------------
# Fuzzy suggestion logic (keep)
# ----------------------------

def fuzzy_suggest(input_topic: str, knowledge: Dict[str, Any], aliases: Dict[str, str]) -> List[str]:
    t = input_topic.strip().lower()
    if not t:
        return []

    candidates = set()
    for k in knowledge.keys():
        candidates.add(k)
    for _a, target in aliases.items():
        candidates.add(target)

    cand_list = sorted(candidates)
    matches = difflib.get_close_matches(t, cand_list, n=FUZZY_SUGGEST_N, cutoff=FUZZY_CUTOFF)
    return matches


def set_last_suggestion(alias_word: str, target_topic: str, reason: str) -> None:
    global _last_suggestion
    _last_suggestion = {"alias": alias_word, "target": target_topic, "reason": reason}


def clear_last_suggestion() -> None:
    global _last_suggestion
    _last_suggestion = None


# ----------------------------
# Web: HTTP helpers
# ----------------------------

def http_get(url: str, timeout: int = HTTP_TIMEOUT) -> Tuple[int, bytes, str]:
    if urllib is None:
        raise RuntimeError("urllib is not available in this Python environment")

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": DEFAULT_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = getattr(resp, "status", 200)
        final_url = resp.geturl()
        data = resp.read(MAX_PAGE_BYTES + 1)
        if len(data) > MAX_PAGE_BYTES:
            data = data[:MAX_PAGE_BYTES]
        return status, data, final_url


def decode_bytes(data: bytes, fallback: str = "utf-8") -> str:
    if not data:
        return ""
    try:
        return data.decode("utf-8", errors="replace")
    except Exception:
        try:
            return data.decode("latin-1", errors="replace")
        except Exception:
            return data.decode(fallback, errors="replace")


def strip_html(html: str) -> str:
    if not html:
        return ""

    html = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
    html = re.sub(r"(?is)<style.*?>.*?</style>", " ", html)
    html = re.sub(r"(?is)<noscript.*?>.*?</noscript>", " ", html)

    html = re.sub(r"(?is)<(nav|footer|header|aside).*?>.*?</\1>", " ", html)

    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"(?i)</p\s*>", "\n", html)
    html = re.sub(r"(?i)</div\s*>", "\n", html)
    html = re.sub(r"(?i)</li\s*>", "\n", html)

    text = re.sub(r"(?s)<.*?>", " ", html)

    text = text.replace("&nbsp;", " ")
    text = text.replace("&amp;", "&")
    text = text.replace("&quot;", "\"")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")

    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def pick_best_excerpt(text: str, max_chars: int = MAX_NOTE_CHARS) -> str:
    if not text:
        return ""
    t = text.strip()
    if len(t) <= max_chars:
        return t
    cut = t[:max_chars]
    idx = cut.rfind("\n\n")
    if idx > 800:
        cut = cut[:idx].strip()
    return cut.strip()


# ----------------------------
# Web: Search providers (fallback chain)
# ----------------------------

def search_wikipedia_opensearch(query: str) -> List[str]:
    if urllib is None:
        return []

    q = urllib.parse.quote(query)
    url = (
        "https://en.wikipedia.org/w/api.php"
        f"?action=opensearch&search={q}&limit=5&namespace=0&format=json"
    )
    try:
        status, raw, _final = http_get(url, timeout=SEARCH_TIMEOUT)
        if status >= 400:
            return []
        s = decode_bytes(raw)
        data = json.loads(s)
        urls = data[3] if isinstance(data, list) and len(data) >= 4 else []
        if not isinstance(urls, list):
            return []
        return [u for u in urls if isinstance(u, str) and u.startswith("http")]
    except Exception:
        return []


def parse_ddg_links_from_html(html: str) -> List[str]:
    if not html:
        return []

    links: List[str] = []

    for m in re.finditer(r'(?is)<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"', html):
        href = m.group(1).strip()
        links.append(href)

    for m in re.finditer(r'(?is)href="(/l/\?[^"]*uddg=[^"&]+[^"]*)"', html):
        href = m.group(1)
        links.append("https://lite.duckduckgo.com" + href)

    for m in re.finditer(r'(?is)href="([^"]+uddg=[^"]+)"', html):
        href = m.group(1).strip()
        links.append(href)

    out: List[str] = []
    seen = set()
    for u in links:
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def resolve_ddg_redirect(url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        if "uddg" in qs and qs["uddg"]:
            return urllib.parse.unquote(qs["uddg"][0])
    except Exception:
        pass
    return url


def search_duckduckgo_html(query: str) -> List[str]:
    if urllib is None:
        return []
    q = urllib.parse.quote(query)
    url = f"https://duckduckgo.com/html/?q={q}"
    try:
        status, raw, _final = http_get(url, timeout=SEARCH_TIMEOUT)
        if status >= 400:
            return []
        html = decode_bytes(raw)
        links = parse_ddg_links_from_html(html)
        cleaned: List[str] = []
        for u in links:
            u = resolve_ddg_redirect(u)
            if u.startswith("http"):
                cleaned.append(u)
        return cleaned[:MAX_SEARCH_RESULTS]
    except Exception:
        return []


def search_duckduckgo_lite(query: str) -> List[str]:
    if urllib is None:
        return []
    q = urllib.parse.quote(query)
    url = f"https://lite.duckduckgo.com/lite/?q={q}"
    try:
        status, raw, _final = http_get(url, timeout=SEARCH_TIMEOUT)
        if status >= 400:
            return []
        html = decode_bytes(raw)
        links = parse_ddg_links_from_html(html)
        cleaned: List[str] = []
        for u in links:
            u = resolve_ddg_redirect(u)
            if u.startswith("http"):
                cleaned.append(u)
        return cleaned[:MAX_SEARCH_RESULTS]
    except Exception:
        return []


def web_search(query: str) -> Tuple[List[str], str]:
    q = normalize_topic(query)

    urls = search_wikipedia_opensearch(q)
    if urls:
        return urls, "wikipedia_opensearch"

    urls = search_duckduckgo_html(q)
    if urls:
        return urls, "duckduckgo_html"

    urls = search_duckduckgo_lite(q)
    if urls:
        return urls, "duckduckgo_lite"

    return [], "none"


# ----------------------------
# Web: ingestion and learning
# ----------------------------

def note_path_for_topic(topic: str) -> str:
    slug = safe_slug(topic)
    return os.path.join(RESEARCH_NOTES_DIR, f"{slug}.txt")


def write_research_note(topic: str, provider: str, urls: List[str], notes_text: str) -> str:
    ensure_dirs()
    path = note_path_for_topic(topic)
    header = []
    header.append(f"TOPIC: {topic}")
    header.append(f"CREATED: {now_iso()}")
    header.append(f"SEARCH_PROVIDER: {provider}")
    header.append("SOURCES:")
    for u in urls:
        header.append(f"- {u}")
    header.append("")
    content = "\n".join(header) + notes_text.strip() + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def fetch_and_extract(url: str) -> Tuple[bool, str, str]:
    try:
        status, raw, final_url = http_get(url, timeout=HTTP_TIMEOUT)
        if status >= 400:
            return False, final_url, f"HTTP {status}"
        html = decode_bytes(raw)
        text = strip_html(html)
        text = pick_best_excerpt(text, MAX_NOTE_CHARS)
        if len(text) < MIN_TEXT_CHARS:
            return False, final_url, f"weak_text({len(text)} chars)"
        return True, final_url, text
    except urllib.error.HTTPError as e:
        return False, url, f"HTTPError {getattr(e, 'code', 'unknown')}"
    except urllib.error.URLError as e:
        return False, url, f"URLError {str(e)}"
    except Exception as e:
        return False, url, f"error {str(e)}"


def build_simple_answer_from_notes(topic: str, note_text: str, sources: List[str]) -> str:
    t = note_text.strip()
    if not t:
        return f"I collected some web notes for '{topic}', but the extracted text was empty."

    parts = [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]
    excerpt = ""
    if parts:
        excerpt = parts[0]
        if len(parts) >= 2 and len(parts[1]) > 120 and len(excerpt) < 1200:
            excerpt = excerpt + "\n\n" + parts[1]
    if not excerpt:
        excerpt = t[:1200].strip()

    src_lines = "\n".join([f"- {u}" for u in sources[:6]]) if sources else "- (no sources captured)"
    answer = f"{excerpt}\n\nSources I pulled from:\n{src_lines}"
    return answer.strip()


def weblearn_topic(topic: str, update_knowledge: bool = True) -> Tuple[bool, str]:
    t = normalize_topic(topic)

    bad, reason = is_garbage_topic(t)
    if bad:
        if reason == "ignored test topic":
            return False, "That looks like a test topic. I am ignoring it."
        msg = f"Refusing to web-learn that topic ({reason}). Use a normal topic phrase, not a URL or command."
        return False, msg

    urls, provider = web_search(t)
    if not urls:
        return False, "Web search returned no results or could not be fetched."

    fetched_texts: List[str] = []
    final_sources: List[str] = []

    for u in urls[:MAX_FETCH_PAGES]:
        ok, final_url, text_or_err = fetch_and_extract(u)
        if ok:
            fetched_texts.append(text_or_err)
            final_sources.append(final_url)
        else:
            log_line(WEBQUEUE_LOG, f"fetch_skip topic='{t}' url='{u}' final='{final_url}' reason='{text_or_err}'")

        if len(fetched_texts) >= 2:
            break

    if not fetched_texts:
        return False, "Search worked, but the pages I tried were blocked/too thin to extract."

    combined = "\n\n---\n\n".join(fetched_texts).strip()
    note_path = write_research_note(t, provider, final_sources, "\n" + combined + "\n")

    if update_knowledge:
        knowledge = load_knowledge()
        entry = knowledge.get(t, {})
        old_conf = float(entry.get("confidence", 0.30) or 0.30)
        new_conf = clamp(old_conf + 0.18, 0.10, 0.95)

        answer = build_simple_answer_from_notes(t, combined, final_sources)
        entry = {
            "answer": answer,
            "confidence": new_conf,
            "sources": final_sources,
            "updated_on": today_str(),
            "notes": f"Upgraded using web research note: {os.path.basename(note_path)}",
        }
        knowledge[t] = entry
        save_knowledge(knowledge)

    return True, f"Learned from the web and saved notes: {os.path.relpath(note_path, BASE_DIR)}"


# ----------------------------
# Research queue
# ----------------------------

def queue_topic(topic: str, reason: str, current_conf: float = 0.30) -> Tuple[bool, str]:
    t = normalize_topic(topic).lower()
    bad, why = is_garbage_topic(t)
    if bad:
        if why == "ignored test topic":
            return False, "Not queued (ignored test topic)."
        return False, f"Not queued ({why})."

    q = load_research_queue()

    for item in q:
        if item.get("topic") == t and item.get("status") in ("pending", "running"):
            return False, "Already pending in web queue."

    q.append({
        "topic": t,
        "reason": reason,
        "requested_on": today_str(),
        "status": "pending",
        "current_confidence": float(current_conf),
        "worker_note": "",
        "attempts": 0,
        "last_error": "",
    })
    save_research_queue(q)
    return True, "Queued for deeper web research."


def clear_pending_queue() -> int:
    q = load_research_queue()
    before = len(q)
    q = [x for x in q if x.get("status") != "pending"]
    save_research_queue(q)
    return before - len(q)


def purge_junk_pending() -> Dict[str, int]:
    """
    Removes ONLY pending junk items (commands, URLs, transcript junk).
    Keeps done history.
    """
    q = load_research_queue()
    kept = []
    removed = 0
    for it in q:
        if not isinstance(it, dict):
            continue
        status = it.get("status")
        topic = str(it.get("topic", "") or "")
        if status == "pending":
            bad, _why = is_garbage_topic(topic)
            if bad:
                removed += 1
                continue
        kept.append(it)
    save_research_queue(kept)
    return {"removed": removed, "kept": len(kept)}


def webqueue_run(limit: int = WEBQUEUE_DEFAULT_LIMIT) -> Dict[str, Any]:
    ensure_dirs()
    q = load_research_queue()

    attempted = 0
    learned = 0
    skipped = 0
    failed = 0

    pending_idxs = [i for i, it in enumerate(q) if isinstance(it, dict) and it.get("status") == "pending"]

    if not pending_idxs:
        log_line(WEBQUEUE_LOG, f"run_webqueue: learned=0 attempted=0 skipped=0 failed=0 limit={limit} (no pending)")
        return {"learned": 0, "attempted": 0, "skipped": 0, "failed": 0, "limit": limit, "note": "no pending"}

    for idx in pending_idxs:
        if attempted >= limit:
            break

        item = q[idx]
        topic = str(item.get("topic", "") or "").strip()

        bad, why = is_garbage_topic(topic)
        if bad:
            skipped += 1
            item["status"] = "failed"
            item["last_error"] = f"skipped: {why}"
            item["attempts"] = int(item.get("attempts", 0)) + 1
            q[idx] = item
            log_line(WEBQUEUE_LOG, f"webqueue_skip topic='{topic}' reason='{why}'")
            continue

        attempted += 1
        item["status"] = "running"
        item["attempts"] = int(item.get("attempts", 0)) + 1
        q[idx] = item
        save_research_queue(q)

        ok, msg = weblearn_topic(topic, update_knowledge=True)
        if ok:
            learned += 1
            item["status"] = "done"
            item["completed_on"] = today_str()
            item["worker_note"] = "Upgraded knowledge using web research note"
            item["last_error"] = ""
            q[idx] = item
            log_line(WEBQUEUE_LOG, f"webqueue_done topic='{topic}' msg='{msg}'")
        else:
            failed += 1
            item["status"] = "failed"
            item["last_error"] = msg
            q[idx] = item
            log_line(WEBQUEUE_LOG, f"webqueue_failed topic='{topic}' error='{msg}'")

        save_research_queue(q)

    log_line(WEBQUEUE_LOG, f"run_webqueue: learned={learned} attempted={attempted} skipped={skipped} failed={failed} limit={limit}")
    return {"learned": learned, "attempted": attempted, "skipped": skipped, "failed": failed, "limit": limit}


# ----------------------------
# Commands
# ----------------------------

def cmd_help() -> None:
    print(f"{APP_NAME} commands:")
    print("  /teach <topic> | <answer>")
    print("  /teachfile <topic> | <path_to_text_file>")
    print("  /import <path_to_json>")
    print("  /importfolder <folder_path>")
    print("  /ingest <topic> | <path_to_text_file>")
    print("  /export")
    print("  /queue")
    print("  /clearpending")
    print("  /purgejunk    (remove ONLY pending junk items like cat/tail/urls)")
    print("  /promote <topic> [confidence]")
    print("  /confidence <topic>")
    print("  /lowest [n]")
    print("  /alias <alias> | <target_topic>")
    print("  /aliases")
    print("  /unalias <alias>")
    print("  /accept  (accept last alias suggestion)")
    print("  /suggest (show last suggestion)")
    print("  /why <topic>")
    print("  /weblearn <topic>")
    print("  /webqueue [limit]")
    print("  /curiosity   (simple daily low-confidence queue fill)")
    print("  /exit")


def parse_pipe_args(s: str) -> Optional[Tuple[str, str]]:
    if "|" not in s:
        return None
    left, right = s.split("|", 1)
    return left.strip(), right.strip()


def cmd_teach(arg: str) -> None:
    parsed = parse_pipe_args(arg)
    if not parsed:
        print("Usage: /teach <topic> | <answer>")
        return
    topic, answer = parsed
    topic = normalize_topic(topic).lower()
    if not topic:
        print("Topic cannot be empty.")
        return
    if not answer.strip():
        print("Answer cannot be empty.")
        return

    knowledge = load_knowledge()
    entry = knowledge.get(topic, {})
    old_conf = float(entry.get("confidence", 0.30) or 0.30)
    new_conf = clamp(old_conf + 0.25, 0.10, 0.98)

    entry = {
        "answer": answer.strip(),
        "confidence": new_conf,
        "sources": entry.get("sources", []),
        "updated_on": today_str(),
        "notes": "Updated by user via /teach",
    }
    knowledge[topic] = entry
    save_knowledge(knowledge)
    print(f"Learned: '{topic}' (confidence {pretty_conf(new_conf)})")


def cmd_teachfile(arg: str) -> None:
    parsed = parse_pipe_args(arg)
    if not parsed:
        print("Usage: /teachfile <topic> | <path_to_text_file>")
        return
    topic, path = parsed
    topic = normalize_topic(topic).lower()
    path = path.strip()

    if not os.path.exists(path):
        print(f"File not found: {path}")
        return

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read().strip()
    except Exception as e:
        print(f"Could not read file: {e}")
        return

    if not content:
        print("File was empty.")
        return

    knowledge = load_knowledge()
    entry = knowledge.get(topic, {})
    old_conf = float(entry.get("confidence", 0.30) or 0.30)
    new_conf = clamp(old_conf + 0.22, 0.10, 0.98)

    entry = {
        "answer": content,
        "confidence": new_conf,
        "sources": entry.get("sources", []),
        "updated_on": today_str(),
        "notes": f"Updated by user via /teachfile ({os.path.basename(path)})",
    }
    knowledge[topic] = entry
    save_knowledge(knowledge)
    print(f"Learned from file: '{topic}' (confidence {pretty_conf(new_conf)})")


def cmd_ingest(arg: str) -> None:
    parsed = parse_pipe_args(arg)
    if not parsed:
        print("Usage: /ingest <topic> | <path_to_text_file>")
        return
    topic, path = parsed
    topic = normalize_topic(topic).lower()
    path = path.strip()

    if not os.path.exists(path):
        print(f"File not found: {path}")
        return

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read().strip()
    except Exception as e:
        print(f"Could not read file: {e}")
        return

    if not content:
        print("File was empty.")
        return

    note_path = write_research_note(topic, "local_ingest", [f"file:{os.path.abspath(path)}"], "\n" + content + "\n")

    knowledge = load_knowledge()
    entry = knowledge.get(topic, {})
    old_conf = float(entry.get("confidence", 0.30) or 0.30)
    new_conf = clamp(old_conf + 0.18, 0.10, 0.98)

    entry = {
        "answer": content,
        "confidence": new_conf,
        "sources": [f"file:{os.path.abspath(path)}"],
        "updated_on": today_str(),
        "notes": f"Ingested local file into research note: {os.path.basename(note_path)}",
    }
    knowledge[topic] = entry
    save_knowledge(knowledge)

    print(f"Ingested into notes + knowledge: '{topic}' (confidence {pretty_conf(new_conf)})")


def cmd_import(path: str) -> None:
    path = path.strip()
    if not path:
        print("Usage: /import <path_to_json>")
        return
    if not os.path.exists(path):
        print(f"File not found: {path}")
        return

    data = load_json(path, None)
    if data is None or not isinstance(data, dict):
        print("Import failed: JSON must be an object of topic -> entry.")
        return

    knowledge = load_knowledge()
    merged = 0
    for k, v in data.items():
        if not isinstance(k, str):
            continue
        kk = normalize_topic(k).lower()
        if isinstance(v, dict) and "answer" in v:
            knowledge[kk] = v
            merged += 1
        elif isinstance(v, str):
            knowledge[kk] = {"answer": v, "confidence": 0.50, "sources": [], "updated_on": today_str(), "notes": "Imported string entry"}
            merged += 1

    save_knowledge(knowledge)
    print(f"Imported {merged} topics.")


def cmd_importfolder(folder: str) -> None:
    folder = folder.strip()
    if not folder:
        print("Usage: /importfolder <folder_path>")
        return
    if not os.path.isdir(folder):
        print(f"Folder not found: {folder}")
        return

    knowledge = load_knowledge()
    count = 0
    for root, _dirs, files in os.walk(folder):
        for fn in files:
            if not fn.lower().endswith(".txt"):
                continue
            path = os.path.join(root, fn)
            topic = os.path.splitext(fn)[0].replace("_", " ").strip().lower()
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read().strip()
                if not content:
                    continue
                knowledge[topic] = {
                    "answer": content,
                    "confidence": 0.55,
                    "sources": [f"file:{os.path.abspath(path)}"],
                    "updated_on": today_str(),
                    "notes": "Imported from folder",
                }
                count += 1
            except Exception:
                continue

    save_knowledge(knowledge)
    print(f"Imported {count} text files from folder into knowledge.")


def cmd_export() -> None:
    ensure_dirs()
    knowledge = load_knowledge()
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(EXPORTS_DIR, f"knowledge_export_{ts}.json")
    save_json(out, knowledge)
    print(f"Exported knowledge to: {os.path.relpath(out, BASE_DIR)}")


def cmd_queue() -> None:
    q = load_research_queue()
    if not q:
        print("Research queue is empty.")
        return
    print("Research queue (latest first):")
    for item in list(reversed(q[-15:])):
        topic = item.get("topic", "")
        status = item.get("status", "")
        reason = item.get("reason", "")
        attempts = item.get("attempts", 0)
        err = item.get("last_error", "")
        print(f"- {topic} [{status}] attempts={attempts} reason='{reason}'")
        if err:
            print(f"  last_error: {err}")


def cmd_clearpending() -> None:
    removed = clear_pending_queue()
    print(f"Removed {removed} pending items from web research queue.")


def cmd_purgejunk() -> None:
    res = purge_junk_pending()
    print(f"Purged junk pending items. removed={res['removed']} kept={res['kept']}")


def cmd_promote(arg: str) -> None:
    parts = arg.strip().split()
    if not parts:
        print("Usage: /promote <topic> [confidence]")
        return
    topic = normalize_topic(" ".join(parts[:-1] if len(parts) > 1 and re.match(r"^\d+(\.\d+)?$", parts[-1]) else parts)).lower()
    new_conf = None
    if len(parts) > 1 and re.match(r"^\d+(\.\d+)?$", parts[-1]):
        try:
            new_conf = float(parts[-1])
        except Exception:
            new_conf = None

    knowledge = load_knowledge()
    if topic not in knowledge:
        print("No entry yet for that topic. Teach it first.")
        return

    entry = knowledge.get(topic, {})
    old = float(entry.get("confidence", 0.30) or 0.30)
    if new_conf is None:
        new = clamp(old + 0.10, 0.10, 0.99)
    else:
        new = clamp(new_conf, 0.10, 0.99)

    entry["confidence"] = new
    entry["updated_on"] = today_str()
    entry["notes"] = "Promoted confidence via /promote"
    knowledge[topic] = entry
    save_knowledge(knowledge)
    print(f"Promoted '{topic}' confidence: {pretty_conf(old)} -> {pretty_conf(new)}")


def cmd_confidence(topic: str) -> None:
    t = normalize_topic(topic).lower()
    knowledge = load_knowledge()
    entry = knowledge.get(t)
    if not entry:
        print("No entry for that topic.")
        return
    print(f"{t}: confidence={pretty_conf(entry.get('confidence', 0.0))}")


def cmd_lowest(arg: str) -> None:
    n = 10
    arg = arg.strip()
    if arg:
        try:
            n = int(arg)
        except Exception:
            n = 10
    n = max(1, min(50, n))

    knowledge = load_knowledge()
    items = []
    for k, v in knowledge.items():
        try:
            c = float(v.get("confidence", 0.0) or 0.0)
        except Exception:
            c = 0.0
        items.append((c, k))
    items.sort(key=lambda x: x[0])
    print(f"Lowest confidence topics (top {n}):")
    for c, k in items[:n]:
        print(f"- {k}: {pretty_conf(c)}")


def cmd_alias(arg: str) -> None:
    parsed = parse_pipe_args(arg)
    if not parsed:
        print("Usage: /alias <alias> | <target_topic>")
        return
    alias_word, target = parsed
    alias_word = normalize_topic(alias_word).lower()
    target = normalize_topic(target).lower()

    if not alias_word or not target:
        print("Alias and target cannot be empty.")
        return

    aliases = load_aliases()
    aliases[alias_word] = target
    save_aliases(aliases)
    print(f"Alias set: {alias_word} -> {target}")


def cmd_aliases() -> None:
    aliases = load_aliases()
    if not aliases:
        print("No aliases set.")
        return
    print("Aliases:")
    for a in sorted(aliases.keys()):
        print(f"- {a} -> {aliases[a]}")


def cmd_unalias(arg: str) -> None:
    a = normalize_topic(arg).lower()
    if not a:
        print("Usage: /unalias <alias>")
        return
    aliases = load_aliases()
    if a not in aliases:
        print("Alias not found.")
        return
    target = aliases.pop(a)
    save_aliases(aliases)
    print(f"Removed alias: {a} -> {target}")


def cmd_suggest() -> None:
    if not _last_suggestion:
        print("No suggestion available.")
        return
    print(f"Suggestion: /alias {_last_suggestion['alias']} | {_last_suggestion['target']}")
    if _last_suggestion.get("reason"):
        print(f"Reason: {_last_suggestion['reason']}")


def cmd_accept() -> None:
    if not _last_suggestion:
        print("No suggestion to accept.")
        return
    alias_word = _last_suggestion["alias"]
    target = _last_suggestion["target"]
    aliases = load_aliases()
    aliases[alias_word] = target
    save_aliases(aliases)
    print(f"Accepted alias: {alias_word} -> {target}")
    clear_last_suggestion()


def cmd_why(arg: str) -> None:
    topic = normalize_topic(arg).lower()
    if not topic:
        print("Usage: /why <topic>")
        return
    aliases = load_aliases()
    resolved = resolve_alias(topic, aliases).lower()

    knowledge = load_knowledge()
    entry = knowledge.get(resolved)
    if not entry:
        print("No entry for that topic.")
        return

    print(f"WHY for '{resolved}':")
    print(f"- confidence: {pretty_conf(entry.get('confidence', 0.0))}")
    note = entry.get("notes", "")
    if note:
        print(f"- notes: {note}")
    srcs = entry.get("sources", [])
    if srcs:
        print("- sources:")
        for u in srcs[:8]:
            print(f"  - {u}")
    else:
        print("- sources: (none recorded)")


def cmd_weblearn(arg: str) -> None:
    topic = normalize_topic(arg)
    ok, msg = weblearn_topic(topic, update_knowledge=True)
    print(msg)


def cmd_webqueue(arg: str) -> None:
    limit = WEBQUEUE_DEFAULT_LIMIT
    s = arg.strip()
    if s:
        try:
            limit = int(s)
        except Exception:
            limit = WEBQUEUE_DEFAULT_LIMIT
    limit = max(1, min(25, limit))
    res = webqueue_run(limit=limit)
    print(f"Web queue run complete. Learned {res['learned']} out of {res['attempted']} attempted (limit {res['limit']}).")
    if res.get("skipped", 0) or res.get("failed", 0):
        print(f"Skipped={res.get('skipped',0)} Failed={res.get('failed',0)} (see {os.path.relpath(WEBQUEUE_LOG, BASE_DIR)})")


def cmd_curiosity() -> None:
    """
    Simple: queue low-confidence topics that are not already pending.
    (This is intentionally light-weight.)
    """
    knowledge = load_knowledge()
    q = load_research_queue()
    pending = {str(it.get("topic", "")) for it in q if isinstance(it, dict) and it.get("status") == "pending"}

    queued = 0
    for topic, entry in knowledge.items():
        try:
            conf = float(entry.get("confidence", 0.30) or 0.30)
        except Exception:
            conf = 0.30
        if conf < 0.50 and topic not in pending:
            bad, _why = is_garbage_topic(topic)
            if bad:
                continue
            ok, _msg = queue_topic(topic, reason="Curiosity maintenance: low confidence topic", current_conf=conf)
            if ok:
                queued += 1

    log_line(CURIOSITY_LOG, f"curiosity_manual_run queued={queued}")
    print(f"Curiosity queued={queued}")


# ----------------------------
# Chat / answering behavior
# ----------------------------

def should_queue_for_research(topic: str, knowledge: Dict[str, Any]) -> Tuple[bool, str, float]:
    entry = knowledge.get(topic)
    if not entry:
        return True, "No taught answer yet", 0.30

    try:
        conf = float(entry.get("confidence", 0.30) or 0.30)
    except Exception:
        conf = 0.30

    if conf < MIN_CONFIDENCE_FOR_NO_QUEUE:
        return True, "Answer exists but confidence is low", conf

    return False, "", conf


def respond_to_topic(user_text: str) -> None:
    raw = normalize_topic(user_text)
    if not raw:
        return

    bad, why = is_garbage_topic(raw)
    if bad:
        if why == "ignored test topic":
            print(f"{APP_NAME}: That looks like a test topic. I am ignoring it.")
            clear_last_suggestion()
            return
        if why == "looks like a terminal command":
            print(f"{APP_NAME}: That looks like a terminal command. I will not treat it as a topic, alias, or research queue item. Run it in your terminal, or ask me what it does.")
            clear_last_suggestion()
            return
        if why == "looks like a URL/domain":
            print(f"{APP_NAME}: That looks like a URL or domain string. Ask using a normal topic name instead (example: 'rfc 1918').")
            clear_last_suggestion()
            return
        print(f"{APP_NAME}: I am ignoring that input ({why}).")
        clear_last_suggestion()
        return

    topic = raw.lower()

    aliases = load_aliases()
    resolved = resolve_alias(topic, aliases).lower()

    knowledge = load_knowledge()

    entry = knowledge.get(resolved)
    if entry and entry.get("answer"):
        print(f"{APP_NAME}: {entry['answer']}")
        clear_last_suggestion()

        qit, reason, conf = should_queue_for_research(resolved, knowledge)
        if qit:
            ok, _qmsg = queue_topic(resolved, reason=reason, current_conf=conf)
            if ok:
                log_line(CURIOSITY_LOG, f"queued topic='{resolved}' reason='{reason}' conf={conf}")
        return

    suggestions = fuzzy_suggest(resolved, knowledge, aliases)
    if suggestions:
        best = suggestions[0]
        set_last_suggestion(alias_word=topic, target_topic=best, reason="fuzzy match")
        print(f"Suggestion: /alias {topic} | {best}")
        print(f"{APP_NAME}: I do not have a taught answer for that yet. If my reply is wrong or weak, correct me in your own words and I will remember it.")
        print("My analysis may be incomplete. If this seems wrong, correct me and I will update my understanding.")
        print("I have also marked this topic for deeper research so I can improve my answer over time.")
    else:
        clear_last_suggestion()
        print(f"{APP_NAME}: I do not have a taught answer for that yet. If my reply is wrong or weak, correct me in your own words and I will remember it.")
        print("My analysis may be incomplete. If this seems wrong, correct me and I will update my understanding.")
        print("I have also marked this topic for deeper research so I can improve my answer over time.")

    qit, reason, conf = should_queue_for_research(resolved, knowledge)
    if qit:
        ok, qmsg = queue_topic(resolved, reason=reason, current_conf=conf)
        if ok:
            log_line(CURIOSITY_LOG, f"queued topic='{resolved}' reason='{reason}' conf={conf}")
        else:
            log_line(CURIOSITY_LOG, f"queue_reject topic='{resolved}' msg='{qmsg}'")


# ----------------------------
# Headless jobs (systemd timers)
# ----------------------------

def run_headless_webqueue(limit: int = WEBQUEUE_DEFAULT_LIMIT) -> int:
    try:
        res = webqueue_run(limit=limit)
        if res.get("attempted", 0) == 0:
            return 1
        if res.get("failed", 0) > 0:
            return 2
        return 0
    except Exception as e:
        log_line(WEBQUEUE_LOG, f"headless_webqueue_exception: {e}")
        return 2


def run_headless_curiosity() -> int:
    try:
        cmd_curiosity()
        return 0
    except Exception as e:
        log_line(CURIOSITY_LOG, f"daily_curiosity_exception: {e}")
        return 1


# ----------------------------
# Main loop
# ----------------------------

def handle_command(line: str) -> bool:
    s = line.strip()
    if not s:
        return True

    s_norm = strip_prompt_prefixes(s)

    if s_norm in ("/exit", "/quit"):
        return False

    if s_norm == "/help":
        cmd_help()
        return True

    cmd, *rest = s_norm.split(" ", 1)
    arg = rest[0] if rest else ""

    if cmd == "/teach":
        cmd_teach(arg)
        return True
    if cmd == "/teachfile":
        cmd_teachfile(arg)
        return True
    if cmd == "/import":
        cmd_import(arg)
        return True
    if cmd == "/importfolder":
        cmd_importfolder(arg)
        return True
    if cmd == "/ingest":
        cmd_ingest(arg)
        return True
    if cmd == "/export":
        cmd_export()
        return True
    if cmd == "/queue":
        cmd_queue()
        return True
    if cmd == "/clearpending":
        cmd_clearpending()
        return True
    if cmd == "/purgejunk":
        cmd_purgejunk()
        return True
    if cmd == "/promote":
        cmd_promote(arg)
        return True
    if cmd == "/confidence":
        cmd_confidence(arg)
        return True
    if cmd == "/lowest":
        cmd_lowest(arg)
        return True
    if cmd == "/alias":
        cmd_alias(arg)
        return True
    if cmd == "/aliases":
        cmd_aliases()
        return True
    if cmd == "/unalias":
        cmd_unalias(arg)
        return True
    if cmd == "/accept":
        cmd_accept()
        return True
    if cmd == "/suggest":
        cmd_suggest()
        return True
    if cmd == "/why":
        cmd_why(arg)
        return True
    if cmd == "/weblearn":
        cmd_weblearn(arg)
        return True
    if cmd == "/webqueue":
        cmd_webqueue(arg)
        return True
    if cmd == "/curiosity":
        cmd_curiosity()
        return True

    if s_norm.startswith("/"):
        print("Unknown command. Type /help")
        return True

    respond_to_topic(s_norm)
    return True


def signal_handler(_signum, _frame) -> None:
    global _shutdown
    _shutdown = True
    print("\nShutting down.")


def main_interactive() -> int:
    ensure_dirs()
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print(f"{APP_NAME} brain online. Type a message, or /help for commands. Ctrl+C to exit.")
    while not _shutdown:
        try:
            line = input("> ")
        except EOFError:
            break
        except KeyboardInterrupt:
            break

        keep_going = handle_command(line)
        if not keep_going:
            break

    print("Shutting down.")
    return 0


def parse_args(argv: List[str]) -> Dict[str, Any]:
    out = {"mode": "interactive", "limit": WEBQUEUE_DEFAULT_LIMIT}
    if len(argv) <= 1:
        return out

    if argv[1] == "--webqueue":
        out["mode"] = "webqueue"
        if len(argv) >= 3:
            try:
                out["limit"] = int(argv[2])
            except Exception:
                out["limit"] = WEBQUEUE_DEFAULT_LIMIT
        return out

    if argv[1] == "--curiosity":
        out["mode"] = "curiosity"
        return out

    return out


if __name__ == "__main__":
    ensure_dirs()
    args = parse_args(sys.argv)

    if args["mode"] == "webqueue":
        rc = run_headless_webqueue(limit=max(1, min(25, int(args.get("limit", WEBQUEUE_DEFAULT_LIMIT)))))
        sys.exit(rc)

    if args["mode"] == "curiosity":
        rc = run_headless_curiosity()
        sys.exit(rc)

    sys.exit(main_interactive())
