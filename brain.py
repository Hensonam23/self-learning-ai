#!/usr/bin/env python3

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

EXPORTS_DIR = os.path.join(DATA_DIR, "exports")
BACKUPS_DIR = os.path.join(DATA_DIR, "backups")

AUTO_INGEST_DIR = os.path.join(DATA_DIR, "auto_ingest")
AUTO_INGEST_STATE_PATH = os.path.join(DATA_DIR, "auto_ingest_state.json")

AUTO_IMPORT_DIR = os.path.join(DATA_DIR, "auto_import")
AUTO_IMPORT_STATE_PATH = os.path.join(DATA_DIR, "auto_import_state.json")

RESEARCH_NOTES_DIR = os.path.join(DATA_DIR, "research_notes")
WEB_CACHE_DIR = os.path.join(DATA_DIR, "web_cache")

LOGS_DIR = os.path.join(DATA_DIR, "logs")
WEBQUEUE_LOG_PATH = os.path.join(LOGS_DIR, "webqueue.log")
CURIOSITY_LOG_PATH = os.path.join(LOGS_DIR, "curiosity.log")

AUTO_STATE_PATH = os.path.join(DATA_DIR, "auto_state.json")


BACKUP_EVERY_SECONDS = 20 * 60  # 20 minutes

FUZZY_SUGGEST_THRESHOLD = 0.72
FUZZY_ACCEPT_THRESHOLD = 0.92
QUEUE_THRESHOLD = 0.58
MIN_CONFIDENCE_FOR_AUTO_ALIAS_TARGET = 0.55

AUTO_NOTES_CONFIDENCE_FLOOR = 0.65

WEBQUEUE_LIMIT_PER_RUN = 3
WEBLEARN_MAX_SOURCES = 5
WEBLEARN_TIMEOUT_SEC = 14
WEBLEARN_PER_SOURCE_CHAR_LIMIT = 14000
WEBLEARN_SLEEP_BETWEEN_FETCH_SEC = 1.0

CURIOSITY_DEFAULT_N = 3
CURIOSITY_MAX_N = 10
CURIOSITY_LOW_CONF_THRESHOLD = 0.70

# URL/domain-ish topic filtering
TOPIC_MIN_LEN_FOR_AUTOQUEUE = 3
BLOCK_TOPIC_IF_LOOKS_LIKE_URL = True


# -------------------------
# Console safety helpers
# -------------------------

def configure_stdio() -> None:
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def safe_print(*args, sep=" ", end="\n") -> None:
    msg = sep.join(str(a) for a in args)
    try:
        print(msg, end=end)
        return
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        try:
            clean = msg.encode(enc, errors="replace").decode(enc, errors="replace")
        except Exception:
            clean = msg.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
        print(clean, end=end)


def now_date() -> str:
    return datetime.date.today().isoformat()


def now_ts() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def ensure_dirs() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(EXPORTS_DIR, exist_ok=True)
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    os.makedirs(AUTO_INGEST_DIR, exist_ok=True)
    os.makedirs(AUTO_IMPORT_DIR, exist_ok=True)
    os.makedirs(RESEARCH_NOTES_DIR, exist_ok=True)
    os.makedirs(WEB_CACHE_DIR, exist_ok=True)
    os.makedirs(LOGS_DIR, exist_ok=True)


def append_log(path: str, line: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out = f"[{stamp}] {line}".rstrip() + "\n"
    try:
        with open(path, "a", encoding="utf-8", errors="replace") as f:
            f.write(out)
    except Exception:
        pass


def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, obj) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def read_text_file(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def write_text_file(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def normalize_topic(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# -------------------------
# Topic safety / hygiene
# -------------------------

def looks_like_url_or_domain(text: str) -> bool:
    """
    True if it looks like a URL, domain, file download, or a raw hostname-like string.
    We do NOT want Curiosity/auto-queue to treat these as topics.
    """
    t = normalize_topic(text)
    if not t:
        return False

    if "://" in t:
        return True
    if t.startswith("www."):
        return True
    if t.startswith("http "):
        return True
    if t.startswith("https "):
        return True

    # contains a slash like a URL path
    if "/" in t and not t.startswith("/"):
        # allow things like "tcp/ip" as a legit concept
        if t not in ("tcp/ip", "ip/tcp"):
            return True

    # ends with common file types or looks like a download
    if re.search(r"\.(pdf|zip|tar|gz|tgz|exe|dmg|apk|iso|bin)\b", t):
        return True

    # domain-like: has dot + TLD-ish
    if re.search(r"\b[a-z0-9-]+\.(com|org|net|edu|gov|mil|io|co|us|uk|de|jp|fr|au|ca)\b", t):
        return True

    return False


def is_ok_for_autoqueue(topic: str) -> Tuple[bool, str]:
    t = normalize_topic(topic)

    if len(t) < TOPIC_MIN_LEN_FOR_AUTOQUEUE:
        return False, "Too short to auto-queue."

    if BLOCK_TOPIC_IF_LOOKS_LIKE_URL and looks_like_url_or_domain(t):
        return False, "Looks like a URL/domain, not a topic."

    return True, ""


def topic_to_notes_filename(topic: str) -> str:
    t = normalize_topic(topic)
    t = t.replace(" ", "_")
    t = re.sub(r"[^a-z0-9_]+", "", t)
    t = re.sub(r"_+", "_", t).strip("_")
    if not t:
        t = "untitled"
    return t + ".txt"


def find_research_note_path(topic: str) -> Optional[str]:
    os.makedirs(RESEARCH_NOTES_DIR, exist_ok=True)

    candidates = []
    candidates.append(os.path.join(RESEARCH_NOTES_DIR, topic_to_notes_filename(topic)))

    norm = normalize_topic(topic)
    norm_safe = re.sub(r"[^a-z0-9_ ]+", "", norm).strip()
    if norm_safe:
        candidates.append(os.path.join(RESEARCH_NOTES_DIR, norm_safe.replace(" ", "_") + ".txt"))

    candidates.append(os.path.join(RESEARCH_NOTES_DIR, topic_to_notes_filename(topic)[:-4] + ".md"))

    for p in candidates:
        if os.path.exists(p) and os.path.isfile(p):
            return p
    return None


# -------------------------
# Terminal command detection
# -------------------------

def is_probably_terminal_command(text: str) -> bool:
    t = text.strip()
    if not t:
        return False

    if t.endswith("?"):
        return False

    lower = t.lower()

    for qword in ("what is", "what does", "how do", "how to", "why does", "explain", "help me"):
        if lower.startswith(qword):
            return False

    if any(ch in t for ch in ["|", "&&", "||", ";", ">", "<", "$(", "`"]):
        return True

    if re.search(r"^[\w-]+@[\w-]+:.*\$\s+", t):
        return True

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

    if re.search(r"\s-\w", t) and (" " in t) and not re.search(r"[.!?]$", t):
        return True

    return False


# -------------------------
# Fuzzy matching and keywords
# -------------------------

def best_fuzzy_match(query: str, candidates: List[str]) -> Tuple[Optional[str], float]:
    best = None
    best_ratio = 0.0
    for c in candidates:
        r = difflib.SequenceMatcher(None, query, c).ratio()
        if r > best_ratio:
            best_ratio = r
            best = c
    return best, best_ratio


STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "else", "for", "to", "of",
    "in", "on", "at", "by", "with", "as", "is", "are", "was", "were", "be", "been",
    "it", "this", "that", "these", "those", "from", "into", "over", "under",
    "you", "your", "we", "our", "they", "their", "he", "she", "them", "his", "her",
    "not", "no", "yes", "can", "could", "should", "would", "will", "may", "might",
    "also", "more", "most", "some", "such", "than", "too", "very",
    "about", "what", "why", "how", "when", "where",
}


def tokenize_words(text: str) -> List[str]:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s\-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    return [w for w in text.split(" ") if w]


def top_keywords(text: str, n: int = 18) -> List[str]:
    words = tokenize_words(text)
    freq: Dict[str, int] = {}
    for w in words:
        if w in STOPWORDS:
            continue
        if len(w) <= 2:
            continue
        freq[w] = freq.get(w, 0) + 1
    items = sorted(freq.items(), key=lambda x: x[1], reverse=True)
    return [k for k, _v in items[:n]]


# -------------------------
# Web learning
# -------------------------

def looks_disallowed_for_weblearn(topic: str) -> Tuple[bool, str]:
    t = normalize_topic(topic)

    if BLOCK_TOPIC_IF_LOOKS_LIKE_URL and looks_like_url_or_domain(t):
        return True, "That looks like a URL/domain, not a topic. Use a plain topic like: 'rfc 1918 private ip ranges'."

    bad_markers = [
        "hack", "exploit", "bypass", "crack", "keylogger", "phishing", "malware",
        "ddos", "steal", "credential", "password dump", "ransomware",
        "how to break into", "break into", "cheat code", "aimbot", "undetectable",
        "make a bomb", "build a bomb", "pipe bomb",
    ]
    for m in bad_markers:
        if m in t:
            return True, "I will not web learn topics related to hacking, malware, or wrongdoing."

    personal_markers = [
        "address of", "phone number", "social security", "ssn", "dox", "doxx",
        "private info", "home address", "credit card", "bank account",
    ]
    for m in personal_markers:
        if m in t:
            return True, "I will not web learn requests for personal or private data."

    return False, ""


def domain_from_url(url: str) -> str:
    m = re.match(r"^https?://([^/]+)", url.strip().lower())
    return m.group(1) if m else ""


def source_quality_score(url: str) -> float:
    d = domain_from_url(url)
    if not d:
        return 0.2
    if d.endswith(".gov") or d.endswith(".edu"):
        return 0.95
    if "nist.gov" in d or "ietf.org" in d or "iso.org" in d or "rfc-editor.org" in d:
        return 0.95
    if "wikipedia.org" in d:
        return 0.80
    if d.startswith("docs.") or "/docs" in url.lower():
        return 0.75
    if any(x in d for x in ["medium.com", "blogspot.", "wordpress."]):
        return 0.35
    return 0.55


def is_bad_source_url(url: str) -> bool:
    u = url.lower().strip()
    if not u.startswith("http"):
        return True
    if any(x in u for x in ["youtube.com", "facebook.com", "instagram.com", "tiktok.com", "reddit.com"]):
        return True
    if re.search(r"\.(pdf|zip|tar|gz|tgz|exe|dmg|apk|iso|bin)\b", u):
        return True
    return False


def ddg_lite_search(query: str, max_results: int = 8) -> List[str]:
    if urllib is None:
        return []

    q = urllib.parse.quote_plus(query)
    url = f"https://lite.duckduckgo.com/lite/?q={q}"

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) MachineSpirit/1.0",
        "Accept": "text/html",
    }

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=WEBLEARN_TIMEOUT_SEC) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return []

    links = re.findall(r'href="(https?://[^"]+)"', html)
    cleaned: List[str] = []
    for u in links:
        if "duckduckgo.com" in u:
            continue
        if "javascript:" in u.lower():
            continue
        if is_bad_source_url(u):
            continue
        if u not in cleaned:
            cleaned.append(u)
        if len(cleaned) >= max_results:
            break
    return cleaned


def strip_html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
    html = re.sub(r"(?is)<style.*?>.*?</style>", " ", html)
    html = re.sub(r"(?is)<noscript.*?>.*?</noscript>", " ", html)

    html = re.sub(r"(?is)<br\s*/?>", "\n", html)
    html = re.sub(r"(?is)</p\s*>", "\n", html)
    html = re.sub(r"(?is)</h[1-6]\s*>", "\n", html)
    html = re.sub(r"(?is)</li\s*>", "\n", html)

    text = re.sub(r"(?is)<.*?>", " ", html)
    text = text.replace("&nbsp;", " ")
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&quot;", '"')
    text = text.replace("&#39;", "'")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def fetch_url_text(url: str) -> str:
    if urllib is None:
        return ""

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) MachineSpirit/1.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=WEBLEARN_TIMEOUT_SEC) as resp:
            raw = resp.read()
            try:
                html = raw.decode("utf-8", errors="ignore")
            except Exception:
                html = raw.decode(errors="ignore")
    except Exception:
        return ""

    text = strip_html_to_text(html)
    if len(text) > WEBLEARN_PER_SOURCE_CHAR_LIMIT:
        text = text[:WEBLEARN_PER_SOURCE_CHAR_LIMIT]
    return text


def cache_path_for_url(url: str) -> str:
    safe = re.sub(r"[^a-z0-9]+", "_", url.lower()).strip("_")
    if len(safe) > 160:
        safe = safe[:160]
    if not safe:
        safe = "url"
    return os.path.join(WEB_CACHE_DIR, safe + ".json")


def get_cached_url_text(url: str, max_age_hours: int = 72) -> Optional[str]:
    p = cache_path_for_url(url)
    if not os.path.exists(p):
        return None
    try:
        payload = load_json(p, None)
        if not isinstance(payload, dict):
            return None
        ts = payload.get("fetched_at", "")
        txt = payload.get("text", "")
        if not ts or not txt:
            return None
        fetched = datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
        age = datetime.datetime.now() - fetched
        if age.total_seconds() > max_age_hours * 3600:
            return None
        return str(txt)
    except Exception:
        return None


def set_cached_url_text(url: str, text: str) -> None:
    p = cache_path_for_url(url)
    payload = {
        "url": url,
        "fetched_at": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "text": text,
    }
    save_json(p, payload)


def _pick_definition_lines(topic: str, combined_text: str, limit: int = 5) -> List[str]:
    t = topic.lower().strip()
    lines = [ln.strip() for ln in combined_text.splitlines() if ln.strip()]
    candidates: List[str] = []

    for ln in lines[:1200]:
        low = ln.lower()
        if len(ln) < 45 or len(ln) > 240:
            continue
        if t and t in low:
            if " is " in low or " refers to " in low or " means " in low:
                candidates.append(ln)
                continue
        if " is " in low and len(low.split()) <= 28:
            candidates.append(ln)
            continue

    out = []
    seen = set()
    for c in candidates:
        key = c.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
        if len(out) >= limit:
            break
    return out


def _make_quick_check_questions(topic: str, keywords: List[str]) -> List[str]:
    base = topic.strip()
    k = [x for x in keywords if len(x) > 3][:6]
    qs = []
    qs.append(f"What problem does {base} solve?")
    qs.append(f"What are the main parts or layers of {base}?")
    if k:
        qs.append(f"How does {base} relate to {k[0]} and {k[1] if len(k) > 1 else k[0]}?")
    else:
        qs.append(f"What is a real world example where {base} shows up?")
    return qs[:3]


def heuristic_structured_answer(topic: str, source_urls: List[str], source_texts: List[str]) -> str:
    combined = "\n\n".join(source_texts)
    keywords = top_keywords(combined, n=18)
    defs = _pick_definition_lines(topic, combined, limit=4)

    definition = defs[0] if defs else f"{topic.strip()} is a concept you will see referenced often in this area."
    extras = defs[1:3] if len(defs) > 1 else []

    parts_guess = []
    if keywords:
        for w in keywords[:10]:
            parts_guess.append(f"- {w}")

    quick_check = _make_quick_check_questions(topic, keywords)

    out = []
    out.append(f"{topic.strip()} (synthesized)")
    out.append("")
    out.append("Definition:")
    out.append(f"- {definition}")
    for x in extras:
        out.append(f"- {x}")

    out.append("")
    out.append("How it works:")
    out.append("- Break it into the main components or layers.")
    out.append("- For each component, say what it is responsible for, and what it is not responsible for.")
    out.append("- When troubleshooting, map the symptom to the layer or component that most likely owns it.")

    out.append("")
    out.append("Examples:")
    out.append("- Write one simple example first, then one realistic example you might see at work.")
    out.append("- If it is a model or standard, explain what changes when you move between layers or parts.")

    out.append("")
    out.append("Common mistakes:")
    out.append("- Mixing up similar sounding terms.")
    out.append("- Memorizing lists without understanding what actually changes in real systems.")
    out.append("- Using the wrong layer or component when troubleshooting.")

    if parts_guess:
        out.append("")
        out.append("Related keywords (from sources):")
        out.extend(parts_guess)

    out.append("")
    out.append("Quick check (answer these to prove you understand it):")
    for q in quick_check:
        out.append(f"- {q}")

    out.append("")
    out.append("Sources used:")
    for u in source_urls[:WEBLEARN_MAX_SOURCES]:
        out.append(f"- {u}")

    return "\n".join(out).strip()


def synthesize_answer(topic: str, source_texts: List[str], source_urls: List[str]) -> str:
    return heuristic_structured_answer(topic, source_urls, source_texts)


# -------------------------
# Brain state and features
# -------------------------

class BrainState:
    def __init__(self):
        ensure_dirs()

        self.knowledge: Dict[str, Dict[str, Any]] = load_json(KNOWLEDGE_PATH, {})
        self.aliases: Dict[str, str] = load_json(ALIASES_PATH, {})
        self.queue: List[Dict[str, Any]] = load_json(QUEUE_PATH, [])

        self.auto_ingest_state: Dict[str, Any] = load_json(AUTO_INGEST_STATE_PATH, {"seen_files": {}})
        self.auto_import_state: Dict[str, Any] = load_json(AUTO_IMPORT_STATE_PATH, {"seen_files": {}})
        self.auto_state: Dict[str, Any] = load_json(AUTO_STATE_PATH, {"last_curiosity_date": ""})

        self.last_why: Dict[str, Any] = {}
        self.last_suggestions: List[Tuple[str, float]] = []
        self.last_user_input: str = ""
        self.last_input_was_terminal: bool = False

        self.last_web_sources: List[str] = []

        self._backup_timer: Optional[threading.Timer] = None
        self._shutdown = False

    def save_all(self) -> None:
        save_json(KNOWLEDGE_PATH, self.knowledge)
        save_json(ALIASES_PATH, self.aliases)
        save_json(QUEUE_PATH, self.queue)
        save_json(AUTO_INGEST_STATE_PATH, self.auto_ingest_state)
        save_json(AUTO_IMPORT_STATE_PATH, self.auto_import_state)
        save_json(AUTO_STATE_PATH, self.auto_state)

    # -------- backups --------

    def start_backup_timer(self) -> None:
        self.stop_backup_timer()

        def tick():
            if self._shutdown:
                return
            try:
                self.make_backup()
            finally:
                self._backup_timer = threading.Timer(BACKUP_EVERY_SECONDS, tick)
                self._backup_timer.daemon = True
                self._backup_timer.start()

        self._backup_timer = threading.Timer(BACKUP_EVERY_SECONDS, tick)
        self._backup_timer.daemon = True
        self._backup_timer.start()

    def stop_backup_timer(self) -> None:
        if self._backup_timer:
            try:
                self._backup_timer.cancel()
            except Exception:
                pass
            self._backup_timer = None

    def make_backup(self) -> None:
        os.makedirs(BACKUPS_DIR, exist_ok=True)
        stamp = now_ts()
        bundle_dir = os.path.join(BACKUPS_DIR, f"backup_{stamp}")
        os.makedirs(bundle_dir, exist_ok=True)

        for p in [
            KNOWLEDGE_PATH, ALIASES_PATH, QUEUE_PATH,
            AUTO_INGEST_STATE_PATH, AUTO_IMPORT_STATE_PATH, AUTO_STATE_PATH
        ]:
            if os.path.exists(p):
                shutil.copy2(p, os.path.join(bundle_dir, os.path.basename(p)))

    # -------- auto ingest --------

    def run_auto_ingest(self) -> None:
        os.makedirs(AUTO_INGEST_DIR, exist_ok=True)
        seen = self.auto_ingest_state.get("seen_files", {})
        changed = False

        for name in sorted(os.listdir(AUTO_INGEST_DIR)):
            path = os.path.join(AUTO_INGEST_DIR, name)
            if not os.path.isfile(path):
                continue

            mtime = os.path.getmtime(path)
            last = seen.get(name)
            if last is not None and float(last) >= float(mtime):
                continue

            try:
                raw = read_text_file(path).strip()
                if not raw:
                    seen[name] = mtime
                    changed = True
                    continue

                lines = raw.splitlines()
                if lines and lines[0].lower().startswith("topic:"):
                    topic = lines[0].split(":", 1)[1].strip()
                    answer = "\n".join(lines[1:]).strip()
                else:
                    topic = os.path.splitext(name)[0]
                    answer = raw

                t = normalize_topic(topic)
                if answer:
                    prev_conf = float(self.knowledge.get(t, {}).get("confidence", 0.45))
                    self.knowledge[t] = {
                        "answer": answer,
                        "confidence": clamp(prev_conf, 0.0, 1.0),
                        "updated_on": now_date(),
                        "notes": f"Auto ingested from {name}",
                    }

                seen[name] = mtime
                changed = True
            except Exception:
                continue

        if changed:
            self.auto_ingest_state["seen_files"] = seen
            self.save_all()

    # -------- auto import --------

    def run_auto_import(self) -> None:
        os.makedirs(AUTO_IMPORT_DIR, exist_ok=True)
        seen = self.auto_import_state.get("seen_files", {})
        changed = False

        for name in sorted(os.listdir(AUTO_IMPORT_DIR)):
            path = os.path.join(AUTO_IMPORT_DIR, name)
            if not (os.path.isfile(path) and name.lower().endswith(".json")):
                continue

            mtime = os.path.getmtime(path)
            last = seen.get(name)
            if last is not None and float(last) >= float(mtime):
                continue

            try:
                payload = load_json(path, None)
                if isinstance(payload, dict):
                    for k, v in payload.items():
                        topic = normalize_topic(str(k))
                        if isinstance(v, str):
                            prev_conf = float(self.knowledge.get(topic, {}).get("confidence", 0.45))
                            self.knowledge[topic] = {
                                "answer": v,
                                "confidence": clamp(prev_conf, 0.0, 1.0),
                                "updated_on": now_date(),
                                "notes": f"Auto imported from {name}",
                            }
                            changed = True
                        elif isinstance(v, dict):
                            answer = str(v.get("answer", "")).strip()
                            if not answer:
                                continue
                            conf = float(v.get("confidence", self.knowledge.get(topic, {}).get("confidence", 0.45)))
                            self.knowledge[topic] = {
                                "answer": answer,
                                "confidence": clamp(conf, 0.0, 1.0),
                                "updated_on": now_date(),
                                "notes": v.get("notes", f"Auto imported from {name}"),
                                "sources": v.get("sources", []),
                            }
                            changed = True

                seen[name] = mtime
                changed = True
            except Exception:
                continue

        if changed:
            self.auto_import_state["seen_files"] = seen
            self.save_all()

    # -------- knowledge ops --------

    def get_entry(self, topic: str) -> Optional[Dict[str, Any]]:
        return self.knowledge.get(normalize_topic(topic))

    def set_entry(self, topic: str, answer: str, confidence: float = 0.55, notes: str = "", sources: Optional[List[str]] = None) -> None:
        t = normalize_topic(topic)
        entry: Dict[str, Any] = {
            "answer": answer.strip(),
            "confidence": clamp(float(confidence), 0.0, 1.0),
            "updated_on": now_date(),
            "notes": notes.strip(),
        }
        if sources:
            entry["sources"] = list(sources)
        self.knowledge[t] = entry
        self.save_all()

    # -------- queue --------

    def promote_if_present(self, topic_norm: str) -> bool:
        t = normalize_topic(topic_norm)
        changed = False
        for item in self.queue:
            if normalize_topic(item.get("topic", "")) == t:
                if item.get("status", "pending") != "done":
                    item["status"] = "done"
                    item["completed_on"] = now_date()
                    changed = True
        if changed:
            self.save_all()
        return changed

    def queue_topic(self, topic: str, reason: str, confidence: float) -> bool:
        t = normalize_topic(topic)
        if not t:
            return False

        ok, why = is_ok_for_autoqueue(t)
        if not ok:
            return False

        for item in self.queue:
            if normalize_topic(item.get("topic", "")) == t and item.get("status", "pending") == "pending":
                return False

        note_path = find_research_note_path(t)
        worker_note = ""
        if note_path is None:
            worker_note = f"Missing research note file: {os.path.join(RESEARCH_NOTES_DIR, topic_to_notes_filename(t))}"

        self.queue.append({
            "topic": t,
            "reason": reason,
            "requested_on": now_date(),
            "status": "pending",
            "current_confidence": float(confidence),
            "worker_note": worker_note,
        })
        self.save_all()
        return True

    def clear_pending(self) -> int:
        before = len(self.queue)
        self.queue = [q for q in self.queue if q.get("status") != "pending"]
        self.save_all()
        return before - len(self.queue)

    # -------- aliases --------

    def add_alias(self, alias: str, target: str) -> None:
        a = normalize_topic(alias)
        t = normalize_topic(target)
        if not a or not t:
            return
        self.aliases[a] = t
        self.save_all()

    def remove_alias(self, alias: str) -> bool:
        a = normalize_topic(alias)
        if a in self.aliases:
            del self.aliases[a]
            self.save_all()
            return True
        return False

    def resolve_alias(self, text: str) -> Optional[str]:
        return self.aliases.get(normalize_topic(text))

    # -------- suggestions --------

    def build_candidate_topics(self) -> List[str]:
        return sorted(set(self.knowledge.keys()))

    def compute_suggestions(self, raw_query: str) -> List[Tuple[str, float]]:
        q = normalize_topic(raw_query)
        candidates = self.build_candidate_topics()
        scored = []
        for c in candidates:
            r = difflib.SequenceMatcher(None, q, c).ratio()
            scored.append((c, r))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:8]

    def maybe_auto_accept_alias(self, raw_query: str, match_topic: str, match_ratio: float) -> bool:
        q = normalize_topic(raw_query)
        if not q or q == match_topic:
            return False
        if q in self.aliases:
            return False
        if match_ratio < FUZZY_ACCEPT_THRESHOLD:
            return False

        entry = self.knowledge.get(match_topic)
        if not entry:
            return False
        conf = float(entry.get("confidence", 0.0))
        if conf < MIN_CONFIDENCE_FOR_AUTO_ALIAS_TARGET:
            return False

        self.add_alias(q, match_topic)
        return True

    # -------- notes upgrade --------

    def upsert_from_notes(self, topic: str, note_path: str) -> bool:
        try:
            txt = read_text_file(note_path).strip()
            if not txt:
                return False
        except Exception:
            return False

        t = normalize_topic(topic)
        existing_conf = float(self.knowledge.get(t, {}).get("confidence", 0.0))
        new_conf = max(existing_conf, AUTO_NOTES_CONFIDENCE_FLOOR)

        self.knowledge[t] = {
            "answer": txt,
            "confidence": clamp(new_conf, 0.0, 1.0),
            "updated_on": now_date(),
            "notes": f"Upgraded from research note: {os.path.basename(note_path)}",
        }

        self.promote_if_present(t)
        self.save_all()
        return True

    def autoupgrade_from_notes(self) -> Tuple[int, int]:
        upgraded = 0
        checked = 0
        for item in self.queue:
            if item.get("status", "pending") != "pending":
                continue
            topic = item.get("topic", "")
            if not topic:
                continue
            checked += 1
            p = find_research_note_path(topic)
            if p:
                if self.upsert_from_notes(topic, p):
                    upgraded += 1
        return upgraded, checked

    # -------- web learn --------

    def weblearn_topic(self, topic: str) -> Tuple[bool, str]:
        disallowed, reason = looks_disallowed_for_weblearn(topic)
        if disallowed:
            return False, reason

        if urllib is None:
            return False, "Web learning is unavailable because urllib is not available in this Python environment."

        urls = ddg_lite_search(topic, max_results=12)
        if not urls:
            return False, "Web search returned no results or could not be fetched."

        filtered = []
        for u in urls:
            if is_bad_source_url(u):
                continue
            filtered.append(u)

        filtered.sort(key=lambda x: source_quality_score(x), reverse=True)

        picked: List[str] = []
        for u in filtered:
            picked.append(u)
            if len(picked) >= WEBLEARN_MAX_SOURCES:
                break

        if not picked:
            return False, "Search results were filtered out. Try a more specific topic."

        texts: List[str] = []
        used: List[str] = []

        for u in picked:
            cached = get_cached_url_text(u)
            if cached is None:
                txt = fetch_url_text(u)
                if txt:
                    set_cached_url_text(u, txt)
                time.sleep(WEBLEARN_SLEEP_BETWEEN_FETCH_SEC)
            else:
                txt = cached

            txt = (txt or "").strip()
            if len(txt) < 400:
                continue

            texts.append(txt)
            used.append(u)

        if not texts:
            return False, "Could not extract enough readable text from sources."

        answer = synthesize_answer(topic, texts, used)

        note_file = os.path.join(RESEARCH_NOTES_DIR, topic_to_notes_filename(topic))
        write_text_file(note_file, answer)

        q_scores = [source_quality_score(u) for u in used]
        base_q = sum(q_scores) / max(1, len(q_scores))

        t = normalize_topic(topic)
        existing_conf = float(self.knowledge.get(t, {}).get("confidence", 0.0))
        new_conf = max(existing_conf, clamp(0.55 + (base_q * 0.35), 0.55, 0.88))

        notes = f"Web learned on {now_date()} using local synthesis"

        self.set_entry(
            topic,
            answer,
            confidence=new_conf,
            notes=notes,
            sources=used,
        )

        self.promote_if_present(topic)
        self.last_web_sources = list(used)

        return True, f"Web learned and saved. Sources used: {len(used)}. Notes file: {os.path.basename(note_file)}"

    def webqueue(self, limit: int = WEBQUEUE_LIMIT_PER_RUN) -> Tuple[int, int]:
        done = 0
        attempted = 0
        for item in self.queue:
            if item.get("status", "pending") != "pending":
                continue
            topic = item.get("topic", "")
            if not topic:
                continue

            # Skip garbage URL-like topics that got into queue from older versions
            if looks_like_url_or_domain(topic):
                item["status"] = "done"
                item["completed_on"] = now_date()
                item["worker_note"] = (item.get("worker_note", "") + " | auto-skipped: url-like topic").strip()
                self.save_all()
                continue

            attempted += 1
            ok, _msg = self.weblearn_topic(topic)
            if ok:
                done += 1
            if attempted >= limit:
                break
        return done, attempted

    # -------- curiosity --------

    def curiosity_queue(self, n: int = CURIOSITY_DEFAULT_N) -> Tuple[int, List[str], str]:
        n = int(n)
        if n < 1:
            n = 1
        if n > CURIOSITY_MAX_N:
            n = CURIOSITY_MAX_N

        today = now_date()
        last = str(self.auto_state.get("last_curiosity_date", "")).strip()
        if last == today:
            return 0, [], "Curiosity already ran today."

        pending = set()
        for item in self.queue:
            if item.get("status", "pending") == "pending":
                pending.add(normalize_topic(item.get("topic", "")))

        candidates: List[Tuple[str, float]] = []
        for t, e in self.knowledge.items():
            try:
                c = float(e.get("confidence", 0.0))
            except Exception:
                c = 0.0

            if c >= CURIOSITY_LOW_CONF_THRESHOLD:
                continue
            if t in pending:
                continue

            ok, _why = is_ok_for_autoqueue(t)
            if not ok:
                continue

            candidates.append((t, c))

        candidates.sort(key=lambda x: x[1])

        picked = [t for (t, _c) in candidates[:n]]
        added = 0
        added_topics: List[str] = []
        for t in picked:
            if self.queue_topic(t, reason="Curiosity maintenance: low confidence topic", confidence=0.35):
                added += 1
                added_topics.append(t)

        self.auto_state["last_curiosity_date"] = today
        self.save_all()

        if added == 0:
            return 0, [], "Curiosity found no topics to add."
        return added, added_topics, "Curiosity queued topics for learning."

    # -------- import/export/ingest --------

    def import_json_file(self, path: str) -> Tuple[int, int]:
        payload = load_json(path, None)
        if not isinstance(payload, dict):
            return 0, 0

        added = 0
        updated = 0
        for k, v in payload.items():
            topic = normalize_topic(str(k))
            if not topic:
                continue

            if isinstance(v, str):
                if topic in self.knowledge:
                    updated += 1
                else:
                    added += 1
                prev_conf = float(self.knowledge.get(topic, {}).get("confidence", 0.45))
                self.knowledge[topic] = {
                    "answer": v,
                    "confidence": clamp(prev_conf, 0.0, 1.0),
                    "updated_on": now_date(),
                    "notes": f"Imported from {os.path.basename(path)}",
                }
            elif isinstance(v, dict):
                answer = str(v.get("answer", "")).strip()
                if not answer:
                    continue
                conf = float(v.get("confidence", self.knowledge.get(topic, {}).get("confidence", 0.45)))
                if topic in self.knowledge:
                    updated += 1
                else:
                    added += 1
                self.knowledge[topic] = {
                    "answer": answer,
                    "confidence": clamp(conf, 0.0, 1.0),
                    "updated_on": now_date(),
                    "notes": v.get("notes", f"Imported from {os.path.basename(path)}"),
                    "sources": v.get("sources", []),
                }

        self.save_all()
        return added, updated

    def import_folder(self, folder: str) -> Tuple[int, int, int]:
        if not os.path.isdir(folder):
            return 0, 0, 0

        files = [f for f in os.listdir(folder) if f.lower().endswith(".json")]
        total_added = 0
        total_updated = 0
        total_files = 0
        for f in sorted(files):
            p = os.path.join(folder, f)
            if not os.path.isfile(p):
                continue
            a, u = self.import_json_file(p)
            total_added += a
            total_updated += u
            total_files += 1
        return total_files, total_added, total_updated

    def ingest_folder_as_topics(self, folder: str) -> Tuple[int, int]:
        if not os.path.isdir(folder):
            return 0, 0

        supported_ext = (".txt", ".md")
        files = [f for f in os.listdir(folder) if f.lower().endswith(supported_ext)]
        added = 0
        updated = 0
        for f in sorted(files):
            p = os.path.join(folder, f)
            if not os.path.isfile(p):
                continue
            topic = os.path.splitext(f)[0]
            content = read_text_file(p).strip()
            if not content:
                continue
            t = normalize_topic(topic)
            prev_conf = float(self.knowledge.get(t, {}).get("confidence", 0.45))
            if t in self.knowledge:
                updated += 1
            else:
                added += 1
            self.knowledge[t] = {
                "answer": content,
                "confidence": clamp(prev_conf, 0.0, 1.0),
                "updated_on": now_date(),
                "notes": f"Ingested from {f}",
            }
        self.save_all()
        return added, updated

    def export_to_folder(self, folder: str) -> str:
        os.makedirs(folder, exist_ok=True)
        out_path = os.path.join(folder, f"knowledge_export_{now_ts()}.json")
        save_json(out_path, self.knowledge)
        return out_path

    # -------- main answering logic --------

    def answer_query(self, raw_query: str) -> str:
        self.last_input_was_terminal = False
        self.last_why = {}
        self.last_suggestions = []
        self.last_user_input = raw_query.strip()

        if is_probably_terminal_command(raw_query):
            self.last_input_was_terminal = True
            self.last_why = {
                "type": "terminal_detected",
                "input": raw_query.strip(),
                "note": "Input looked like a shell command, so alias and queue logic was skipped.",
            }
            return "That looks like a terminal command. I will not treat it as a topic, alias, or research queue item. Run it in your terminal, or ask me what it does."

        q = normalize_topic(raw_query)
        if not q:
            return "Say something, or type /help."

        if q in self.knowledge:
            entry = self.knowledge[q]
            self.last_why = {"type": "exact", "topic": q, "confidence": float(entry.get("confidence", 0.0))}
            return entry.get("answer", "")

        alias_target = self.resolve_alias(q)
        if alias_target and alias_target in self.knowledge:
            entry = self.knowledge[alias_target]
            self.last_why = {"type": "alias", "alias": q, "target": alias_target, "confidence": float(entry.get("confidence", 0.0))}
            return entry.get("answer", "")

        note_path = find_research_note_path(q)
        if note_path:
            if self.upsert_from_notes(q, note_path):
                entry = self.knowledge.get(q, {})
                self.last_why = {
                    "type": "notes_autoupgrade",
                    "topic": q,
                    "note_file": os.path.basename(note_path),
                    "confidence": float(entry.get("confidence", 0.0)),
                }
                return entry.get("answer", "")

        suggestions = self.compute_suggestions(q)
        self.last_suggestions = suggestions

        candidates = self.build_candidate_topics()
        best_topic, best_ratio = best_fuzzy_match(q, candidates)

        if best_topic is not None:
            entry = self.knowledge.get(best_topic, {})
            best_conf = float(entry.get("confidence", 0.0))
            auto_aliased = self.maybe_auto_accept_alias(q, best_topic, best_ratio)

            self.last_why = {
                "type": "fuzzy",
                "input": q,
                "best_topic": best_topic,
                "ratio": best_ratio,
                "best_confidence": best_conf,
                "auto_alias_created": auto_aliased,
            }

            if best_ratio >= 0.84 and "answer" in entry:
                return entry.get("answer", "")

        # Queue hygiene: avoid auto-queueing URLs/domains and short junk
        if len(q) >= TOPIC_MIN_LEN_FOR_AUTOQUEUE:
            top_sug = suggestions[0][1] if suggestions else 0.0
            if (top_sug < 0.84) and (top_sug >= QUEUE_THRESHOLD) and re.search(r"[a-z0-9]", q):
                ok, _why = is_ok_for_autoqueue(q)
                if ok:
                    self.queue_topic(q, reason="No taught answer yet", confidence=0.3)

        return "I do not have a taught answer for that yet. If my reply is wrong or weak, correct me in your own words and I will remember it."


# -------------------------
# CLI command parsing
# -------------------------

HELP_TEXT = f"""
{APP_NAME} brain online.

Commands:
  /help
  /teach <topic> | <answer>
  /teachfile <topic> | <path_to_text_file>

  /import <path_to_json_file>
  /importfolder <folder_path>
  /ingest <folder_path>
  /export <folder_path>

  /queue
  /clearpending
  /promote <topic>

  /weblearn <topic>
  /webqueue
  /sources

  /confidence <topic> [new_value_0_to_1]
  /lowest [n]

  /alias <alias> | <target_topic>
  /aliases
  /unalias <alias>

  /suggest
  /accept <number>
  /why

  /curiosity [n]

Notes:
- Terminal-like inputs are ignored by alias and queue logic.
- /weblearn uses web search plus local synthesis only.
""".strip()


def parse_pipe_args(s: str) -> Tuple[str, str]:
    if "|" not in s:
        return s.strip(), ""
    left, right = s.split("|", 1)
    return left.strip(), right.strip()


def format_suggestions(sugs: List[Tuple[str, float]]) -> str:
    if not sugs:
        return "No suggestions."
    out = []
    for i, (t, r) in enumerate(sugs, start=1):
        out.append(f"{i}) {t} ({r:.2f})")
    return "\n".join(out)


def run_headless_webqueue(limit: int) -> int:
    configure_stdio()
    state = BrainState()
    try:
        state.run_auto_import()
        state.run_auto_ingest()
        upgraded, checked = state.autoupgrade_from_notes()
        done, attempted = state.webqueue(limit=limit)
        state.save_all()
        append_log(WEBQUEUE_LOG_PATH, f"webqueue headless run complete. autoupgrade={upgraded}/{checked} learned={done}/{attempted} limit={limit}")
        return 0
    except Exception as e:
        append_log(WEBQUEUE_LOG_PATH, f"webqueue headless run failed: {repr(e)}")
        return 2


def run_headless_curiosity(n: int) -> int:
    configure_stdio()
    state = BrainState()
    try:
        state.run_auto_import()
        state.run_auto_ingest()
        added, topics, msg = state.curiosity_queue(n=n)
        append_log(CURIOSITY_LOG_PATH, f"{msg} added={added} topics={topics}")
        return 0
    except Exception as e:
        append_log(CURIOSITY_LOG_PATH, f"curiosity headless run failed: {repr(e)}")
        return 2


def main():
    configure_stdio()

    state = BrainState()
    state.run_auto_import()
    state.run_auto_ingest()
    state.start_backup_timer()

    safe_print(f"{APP_NAME} brain online. Type a message, or /help for commands. Ctrl+C to exit.")

    def shutdown(*_args):
        state._shutdown = True
        state.stop_backup_timer()
        state.save_all()
        safe_print("\nShutting down.")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)

    while True:
        try:
            raw = input("> ").rstrip("\n")
        except EOFError:
            shutdown()

        if not raw.strip():
            continue

        state.run_auto_import()
        state.run_auto_ingest()

        if raw.startswith("/"):
            cmdline = raw.strip()

            if cmdline == "/help":
                safe_print(HELP_TEXT)
                continue

            if cmdline.startswith("/teach "):
                rest = cmdline[len("/teach "):].strip()
                topic, answer = parse_pipe_args(rest)
                if not topic or not answer:
                    safe_print("Usage: /teach <topic> | <answer>")
                    continue
                state.set_entry(topic, answer, confidence=0.55, notes="Updated by user re teach")
                safe_print("Saved.")
                continue

            if cmdline.startswith("/teachfile "):
                rest = cmdline[len("/teachfile "):].strip()
                topic, path = parse_pipe_args(rest)
                if not topic or not path:
                    safe_print("Usage: /teachfile <topic> | <path_to_text_file>")
                    continue
                if not os.path.exists(path):
                    safe_print("File not found.")
                    continue
                ans = read_text_file(path).strip()
                if not ans:
                    safe_print("File was empty.")
                    continue
                state.set_entry(topic, ans, confidence=0.55, notes=f"Teachfile from {os.path.basename(path)}")
                safe_print("Saved.")
                continue

            if cmdline.startswith("/import "):
                path = cmdline[len("/import "):].strip()
                if not path:
                    safe_print("Usage: /import <path_to_json_file>")
                    continue
                if not os.path.exists(path):
                    safe_print("File not found.")
                    continue
                added, updated = state.import_json_file(path)
                safe_print(f"Imported. Added {added}, updated {updated}.")
                continue

            if cmdline.startswith("/importfolder "):
                folder = cmdline[len("/importfolder "):].strip()
                if not folder:
                    safe_print("Usage: /importfolder <folder_path>")
                    continue
                files, added, updated = state.import_folder(folder)
                safe_print(f"Imported folder. Files {files}, added {added}, updated {updated}.")
                continue

            if cmdline.startswith("/ingest "):
                folder = cmdline[len("/ingest "):].strip()
                if not folder:
                    safe_print("Usage: /ingest <folder_path>")
                    continue
                added, updated = state.ingest_folder_as_topics(folder)
                safe_print(f"Ingested folder. Added {added}, updated {updated}.")
                continue

            if cmdline.startswith("/export "):
                folder = cmdline[len("/export "):].strip()
                if not folder:
                    safe_print("Usage: /export <folder_path>")
                    continue
                out_path = state.export_to_folder(folder)
                safe_print(f"Exported to {out_path}")
                continue

            if cmdline == "/queue":
                pending = [q for q in state.queue if q.get("status") == "pending"]
                safe_print(f"Pending queue items: {len(pending)}")
                for i, item in enumerate(pending, start=1):
                    safe_print(f"{i}) {item.get('topic')} | {item.get('reason')}")
                continue

            if cmdline == "/clearpending":
                removed = state.clear_pending()
                safe_print(f"Removed {removed} pending items.")
                continue

            if cmdline.startswith("/promote "):
                topic = cmdline[len("/promote "):].strip()
                if not topic:
                    safe_print("Usage: /promote <topic>")
                    continue
                changed = state.promote_if_present(topic)
                safe_print("Promoted." if changed else "No matching queue item found.")
                continue

            if cmdline.startswith("/confidence "):
                rest = cmdline[len("/confidence "):].strip()
                parts = rest.split()
                if not parts:
                    safe_print("Usage: /confidence <topic> [new_value_0_to_1]")
                    continue
                topic = " ".join(parts[:-1]) if len(parts) > 1 else parts[0]
                entry = state.get_entry(topic)
                if entry is None:
                    safe_print("No such topic.")
                    continue
                if len(parts) == 1:
                    safe_print(f"{normalize_topic(topic)} confidence: {float(entry.get('confidence', 0.0)):.2f}")
                    continue
                try:
                    newv = float(parts[-1])
                except Exception:
                    safe_print("Confidence must be a number 0 to 1.")
                    continue
                entry["confidence"] = clamp(newv, 0.0, 1.0)
                entry["updated_on"] = now_date()
                entry["notes"] = (entry.get("notes", "") + " | confidence edited").strip()
                state.knowledge[normalize_topic(topic)] = entry
                state.save_all()
                safe_print("Updated confidence.")
                continue

            if cmdline.startswith("/lowest"):
                rest = cmdline[len("/lowest"):].strip()
                n = 10
                if rest:
                    try:
                        n = int(rest)
                    except Exception:
                        n = 10
                items = []
                for t, e in state.knowledge.items():
                    try:
                        c = float(e.get("confidence", 0.0))
                    except Exception:
                        c = 0.0
                    items.append((t, c))
                items.sort(key=lambda x: x[1])
                safe_print("Lowest confidence topics:")
                for t, c in items[:max(1, n)]:
                    safe_print(f"- {t}: {c:.2f}")
                continue

            if cmdline.startswith("/alias "):
                rest = cmdline[len("/alias "):].strip()
                a, t = parse_pipe_args(rest)
                if not a or not t:
                    safe_print("Usage: /alias <alias> | <target_topic>")
                    continue
                state.add_alias(a, t)
                safe_print("Alias saved.")
                continue

            if cmdline == "/aliases":
                if not state.aliases:
                    safe_print("No aliases.")
                    continue
                safe_print("Aliases:")
                for a in sorted(state.aliases.keys()):
                    safe_print(f"- {a} -> {state.aliases[a]}")
                continue

            if cmdline.startswith("/unalias "):
                a = cmdline[len("/unalias "):].strip()
                if not a:
                    safe_print("Usage: /unalias <alias>")
                    continue
                ok = state.remove_alias(a)
                safe_print("Removed." if ok else "Alias not found.")
                continue

            if cmdline == "/suggest":
                if state.last_input_was_terminal:
                    safe_print("Last input looked like a terminal command. No alias suggestions.")
                    continue
                safe_print("Suggestions:")
                safe_print(format_suggestions(state.last_suggestions))
                safe_print("Use: /accept <number>")
                continue

            if cmdline.startswith("/accept "):
                if state.last_input_was_terminal:
                    safe_print("Last input looked like a terminal command. Refusing to create alias.")
                    continue
                num_s = cmdline[len("/accept "):].strip()
                try:
                    n = int(num_s)
                except Exception:
                    safe_print("Usage: /accept <number>")
                    continue
                if n < 1 or n > len(state.last_suggestions):
                    safe_print("Invalid selection number.")
                    continue
                if not state.last_user_input.strip():
                    safe_print("No last input to alias from.")
                    continue
                alias_from = normalize_topic(state.last_user_input)
                target = state.last_suggestions[n - 1][0]
                state.add_alias(alias_from, target)
                safe_print(f"Accepted. Alias created: {alias_from} -> {target}")
                continue

            if cmdline == "/why":
                if not state.last_why:
                    safe_print("No last decision recorded.")
                else:
                    safe_print(json.dumps(state.last_why, indent=2, ensure_ascii=False))
                continue

            if cmdline.startswith("/weblearn "):
                topic = cmdline[len("/weblearn "):].strip()
                if not topic:
                    safe_print("Usage: /weblearn <topic>")
                    continue
                ok, msg = state.weblearn_topic(topic)
                safe_print(msg)
                continue

            if cmdline == "/webqueue":
                done, attempted = state.webqueue(limit=WEBQUEUE_LIMIT_PER_RUN)
                safe_print(f"Web queue run complete. Learned {done} out of {attempted} attempted (limit {WEBQUEUE_LIMIT_PER_RUN}).")
                continue

            if cmdline == "/sources":
                if not state.last_web_sources:
                    safe_print("No web sources recorded yet.")
                    continue
                safe_print("Last web sources:")
                for u in state.last_web_sources:
                    safe_print(f"- {u}")
                continue

            if cmdline.startswith("/curiosity"):
                rest = cmdline[len("/curiosity"):].strip()
                n = CURIOSITY_DEFAULT_N
                if rest:
                    try:
                        n = int(rest)
                    except Exception:
                        n = CURIOSITY_DEFAULT_N
                added, topics, msg = state.curiosity_queue(n=n)
                safe_print(f"{msg} added={added}")
                for t in topics:
                    safe_print(f"- {t}")
                continue

            safe_print("Unknown command. Type /help.")
            continue

        response = state.answer_query(raw)
        safe_print(f"{APP_NAME}: {response}")


if __name__ == "__main__":
    configure_stdio()

    if "--webqueue" in sys.argv:
        limit = WEBQUEUE_LIMIT_PER_RUN
        if "--limit" in sys.argv:
            try:
                i = sys.argv.index("--limit")
                limit = int(sys.argv[i + 1])
            except Exception:
                limit = WEBQUEUE_LIMIT_PER_RUN
        sys.exit(run_headless_webqueue(limit=limit))

    if "--curiosity" in sys.argv:
        n = CURIOSITY_DEFAULT_N
        if "--n" in sys.argv:
            try:
                i = sys.argv.index("--n")
                n = int(sys.argv[i + 1])
            except Exception:
                n = CURIOSITY_DEFAULT_N
        sys.exit(run_headless_curiosity(n=n))

    main()
