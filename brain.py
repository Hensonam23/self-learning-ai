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


def normalize_topic(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def topic_to_notes_filename(topic: str) -> str:
    t = normalize_topic(topic)
    t = t.replace(" ", "_")
    t = re.sub(r"[^a-z0-9_]+", "", t)
    t = re.sub(r"_+", "_", t).strip("_")
    if not t:
        t = "untitled"
    return t + ".txt"


def read_text_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_text_file(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


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
    first = lower.split()[0] if lower.split() else ""
    if first in common_cmds:
        return True

    if t.startswith("./") or t.startswith("/") or re.match(r"^[A-Za-z]:\\", t):
        return True

    if re.search(r"\s-\w", t) and (" " in t) and not re.search(r"[.!?]$", t):
        return True

    return False


def best_fuzzy_match(query: str, candidates: List[str]) -> Tuple[Optional[str], float]:
    best = None
    best_ratio = 0.0
    for c in candidates:
        r = difflib.SequenceMatcher(None, query, c).ratio()
        if r > best_ratio:
            best_ratio = r
            best = c
    return best, best_ratio


def tokenize_words(text: str) -> List[str]:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s\-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    return [w for w in text.split(" ") if w]


STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "else", "for", "to", "of",
    "in", "on", "at", "by", "with", "as", "is", "are", "was", "were", "be", "been",
    "it", "this", "that", "these", "those", "from", "into", "over", "under",
    "you", "your", "we", "our", "they", "their", "he", "she", "them", "his", "her",
    "not", "no", "yes", "can", "could", "should", "would", "will", "may", "might",
    "also", "more", "most", "some", "such", "than", "too", "very",
    "about", "what", "why", "how", "when", "where",
}


def top_keywords(text: str, n: int = 16) -> List[str]:
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


def looks_disallowed_for_weblearn(topic: str) -> Tuple[bool, str]:
    t = normalize_topic(topic)

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


def synthesize_answer(topic: str, source_texts: List[str], source_urls: List[str]) -> str:
    combined = "\n\n".join(source_texts)
    kws = top_keywords(combined, n=18)

    intro = (
        f"{topic.strip()} in plain terms:\n"
        f"This is a synthesized overview based on multiple sources. "
        f"It is written in my own words and meant to be a clear working explanation.\n"
    )

    bullets = []
    if kws:
        bullets.append("Key ideas you will usually see:")
        for k in kws[:10]:
            bullets.append(f"- {k}")

    guide = []
    guide.append("\nHow to think about it:")
    guide.append("- Start with a simple definition in one sentence.")
    guide.append("- Identify the main parts or layers involved.")
    guide.append("- Explain what problem it solves and where it is used.")
    guide.append("- Mention common examples and common misunderstandings.")

    practical = []
    practical.append("\nPractical notes:")
    practical.append("- If you are learning this for networking or IT work, focus on how it connects to real troubleshooting.")
    practical.append("- If something seems inconsistent across sources, trust primary docs or standards first.")

    sources_block = "\nSources used:\n" + "\n".join([f"- {u}" for u in source_urls[:WEBLEARN_MAX_SOURCES]])

    body = intro + "\n".join(bullets) + "\n" + "\n".join(guide) + "\n" + "\n".join(practical) + "\n\n" + sources_block

    # Make sure we do not accidentally store very long exact snippets.
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body


class BrainState:
    def __init__(self):
        ensure_dirs()

        self.knowledge: Dict[str, Dict[str, Any]] = load_json(KNOWLEDGE_PATH, {})
        self.aliases: Dict[str, str] = load_json(ALIASES_PATH, {})
        self.queue: List[Dict[str, Any]] = load_json(QUEUE_PATH, [])

        self.auto_ingest_state: Dict[str, Any] = load_json(AUTO_INGEST_STATE_PATH, {"seen_files": {}})
        self.auto_import_state: Dict[str, Any] = load_json(AUTO_IMPORT_STATE_PATH, {"seen_files": {}})

        self.last_why: Dict[str, Any] = {}
        self.last_suggestions: List[Tuple[str, float]] = []
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

        for p in [KNOWLEDGE_PATH, ALIASES_PATH, QUEUE_PATH, AUTO_INGEST_STATE_PATH, AUTO_IMPORT_STATE_PATH]:
            if os.path.exists(p):
                shutil.copy2(p, os.path.join(bundle_dir, os.path.basename(p)))

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

                topic_n = normalize_topic(topic)
                if answer:
                    self.knowledge[topic_n] = {
                        "answer": answer,
                        "confidence": float(self.knowledge.get(topic_n, {}).get("confidence", 0.45)),
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
                            self.knowledge[topic] = {
                                "answer": v,
                                "confidence": float(self.knowledge.get(topic, {}).get("confidence", 0.45)),
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
                            }
                            changed = True

                seen[name] = mtime
                changed = True
            except Exception:
                continue

        if changed:
            self.auto_import_state["seen_files"] = seen
            self.save_all()

    def get_entry(self, topic: str) -> Optional[Dict[str, Any]]:
        return self.knowledge.get(normalize_topic(topic))

    def set_entry(self, topic: str, answer: str, confidence: float = 0.55, notes: str = "", sources: Optional[List[str]] = None) -> None:
        t = normalize_topic(topic)
        entry = {
            "answer": answer.strip(),
            "confidence": clamp(float(confidence), 0.0, 1.0),
            "updated_on": now_date(),
            "notes": notes.strip(),
        }
        if sources:
            entry["sources"] = list(sources)
        self.knowledge[t] = entry
        self.save_all()

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

    def queue_topic(self, topic: str, reason: str, confidence: float) -> None:
        t = normalize_topic(topic)
        for item in self.queue:
            if normalize_topic(item.get("topic", "")) == t and item.get("status", "pending") == "pending":
                return

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

    def clear_pending(self) -> int:
        before = len(self.queue)
        self.queue = [q for q in self.queue if q.get("status") != "pending"]
        self.save_all()
        return before - len(self.queue)

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

    def upsert_from_notes(self, topic: str, note_path: str) -> bool:
        try:
            txt = read_text_file(note_path).strip()
            if not txt:
                return False
        except Exception:
            return False

        t = normalize_topic(topic)
        existing_conf = 0.0
        if t in self.knowledge:
            existing_conf = float(self.knowledge[t].get("confidence", 0.0))

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
                ok = self.upsert_from_notes(topic, p)
                if ok:
                    upgraded += 1
        return upgraded, checked

    def weblearn_topic(self, topic: str) -> Tuple[bool, str]:
        disallowed, reason = looks_disallowed_for_weblearn(topic)
        if disallowed:
            return False, reason

        if urllib is None:
            return False, "Web learning is unavailable because urllib is not available in this Python environment."

        urls = ddg_lite_search(topic, max_results=10)
        if not urls:
            return False, "Web search returned no results or could not be fetched."

        picked: List[str] = []
        for u in urls:
            if any(b in u.lower() for b in ["youtube.com", "facebook.com", "instagram.com", "tiktok.com"]):
                continue
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

        # Write research note file so the existing pipeline stays consistent
        note_file = os.path.join(RESEARCH_NOTES_DIR, topic_to_notes_filename(topic))
        write_text_file(note_file, answer)

        t = normalize_topic(topic)
        existing_conf = float(self.knowledge.get(t, {}).get("confidence", 0.0))
        new_conf = max(existing_conf, 0.70)

        self.set_entry(
            topic,
            answer,
            confidence=new_conf,
            notes=f"Web learned on {now_date()}",
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
            attempted += 1
            ok, _msg = self.weblearn_topic(topic)
            if ok:
                done += 1
            if attempted >= limit:
                break
        return done, attempted

    def answer_query(self, raw_query: str) -> str:
        self.last_input_was_terminal = False
        self.last_why = {}
        self.last_suggestions = []

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
            ok = self.upsert_from_notes(q, note_path)
            if ok and q in self.knowledge:
                entry = self.knowledge[q]
                self.last_why = {
                    "type": "notes_autoupgrade",
                    "topic": q,
                    "note_file": os.path.basename(note_path),
                    "confidence": float(entry.get("confidence", 0.0)),
                }
                return entry.get("answer", "")

        candidates = self.build_candidate_topics()
        best_topic, best_ratio = best_fuzzy_match(q, candidates)

        suggestions = self.compute_suggestions(q)
        self.last_suggestions = suggestions

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

        if len(q) >= 3:
            top_sug = suggestions[0][1] if suggestions else 0.0
            if (top_sug < 0.84) and (top_sug >= QUEUE_THRESHOLD) and re.search(r"[a-z0-9]", q):
                self.queue_topic(q, reason="No taught answer yet", confidence=0.3)

        return "I do not have a taught answer for that yet. If my reply is wrong or weak, correct me in your own words and I will remember it."


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
  /autoupgrade
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

Notes:
- Terminal-like inputs are ignored by alias and queue logic.
- /weblearn will search the web and generate an original answer, then store it locally.
- /webqueue will web learn a few pending queue topics each run.
- /sources shows sources used for the last web learned answer.
""".strip()


def parse_pipe_args(s: str) -> Tuple[str, str]:
    if "|" not in s:
        return s.strip(), ""
    left, right = s.split("|", 1)
    return left.strip(), right.strip()


def import_json_file(state: BrainState, path: str) -> int:
    payload = load_json(path, None)
    if not isinstance(payload, dict):
        return 0

    count = 0
    for k, v in payload.items():
        topic = normalize_topic(str(k))
        if isinstance(v, str):
            state.set_entry(
                topic,
                v,
                confidence=float(state.knowledge.get(topic, {}).get("confidence", 0.45)),
                notes=f"Imported from {os.path.basename(path)}",
            )
            count += 1
        elif isinstance(v, dict):
            ans = str(v.get("answer", "")).strip()
            if not ans:
                continue
            conf = float(v.get("confidence", state.knowledge.get(topic, {}).get("confidence", 0.45)))
            notes = str(v.get("notes", f"Imported from {os.path.basename(path)}"))
            sources = v.get("sources", None) if isinstance(v.get("sources", None), list) else None
            state.set_entry(topic, ans, confidence=conf, notes=notes, sources=sources)
            count += 1
    return count


def import_folder(state: BrainState, folder: str) -> int:
    if not os.path.isdir(folder):
        return 0
    total = 0
    for name in sorted(os.listdir(folder)):
        p = os.path.join(folder, name)
        if os.path.isfile(p) and name.lower().endswith(".json"):
            total += import_json_file(state, p)
    return total


def ingest_folder_as_notes(state: BrainState, folder: str) -> int:
    if not os.path.isdir(folder):
        return 0
    total = 0
    for name in sorted(os.listdir(folder)):
        p = os.path.join(folder, name)
        if os.path.isfile(p) and name.lower().endswith(".txt"):
            topic = normalize_topic(os.path.splitext(name)[0])
            ans = read_text_file(p).strip()
            if ans:
                state.set_entry(
                    topic,
                    ans,
                    confidence=float(state.knowledge.get(topic, {}).get("confidence", 0.45)),
                    notes=f"Ingested from {name}",
                )
                total += 1
    return total


def export_knowledge(state: BrainState, folder: str) -> str:
    os.makedirs(folder, exist_ok=True)
    out_path = os.path.join(folder, f"export_{now_ts()}.json")
    save_json(out_path, state.knowledge)
    return out_path


def show_queue(state: BrainState) -> str:
    if not state.queue:
        return "Queue is empty."
    lines = []
    for i, item in enumerate(state.queue, 1):
        t = item.get("topic", "")
        st = item.get("status", "pending")
        rs = item.get("reason", "")
        rd = item.get("requested_on", "")
        cc = item.get("current_confidence", "")
        wn = item.get("worker_note", "")
        extra = f" | note={wn}" if wn else ""
        lines.append(f"{i}. [{st}] {t} (requested {rd}) conf={cc} reason={rs}{extra}")
    return "\n".join(lines)


def promote_topic(state: BrainState, topic: str) -> bool:
    t = normalize_topic(topic)
    changed = False
    for item in state.queue:
        if normalize_topic(item.get("topic", "")) == t:
            item["status"] = "done"
            item["completed_on"] = now_date()
            changed = True
    if changed:
        state.save_all()
    return changed


def set_confidence(state: BrainState, topic: str, conf: float) -> bool:
    t = normalize_topic(topic)
    if t not in state.knowledge:
        return False
    state.knowledge[t]["confidence"] = clamp(float(conf), 0.0, 1.0)
    state.knowledge[t]["updated_on"] = now_date()
    state.save_all()
    return True


def lowest_confidence(state: BrainState, n: int = 10) -> str:
    rows = []
    for t, e in state.knowledge.items():
        rows.append((t, float(e.get("confidence", 0.0))))
    rows.sort(key=lambda x: x[1])
    rows = rows[:max(1, n)]
    out = []
    for i, (t, c) in enumerate(rows, 1):
        out.append(f"{i}. {t}  confidence={c}")
    return "\n".join(out) if out else "No topics found."


def list_aliases(state: BrainState) -> str:
    if not state.aliases:
        return "No aliases saved."
    lines = []
    for a in sorted(state.aliases.keys()):
        lines.append(f"{a} -> {state.aliases[a]}")
    return "\n".join(lines)


def show_suggest(state: BrainState) -> str:
    if state.last_input_was_terminal:
        return "Last input was detected as a terminal command. No alias suggestions."
    if not state.last_suggestions:
        return "No suggestions yet. Ask something first."
    lines = []
    for i, (topic, score) in enumerate(state.last_suggestions, 1):
        if score >= FUZZY_SUGGEST_THRESHOLD:
            lines.append(f"{i}. {topic}  score={round(score, 3)}")
    return "\n".join(lines) if lines else "No strong suggestions."


def accept_suggestion(state: BrainState, num: int, last_raw_input: str) -> str:
    if state.last_input_was_terminal:
        return "Last input was a terminal command. Nothing to accept."
    if not state.last_suggestions:
        return "No suggestions available. Use /suggest after asking something."
    idx = num - 1
    if idx < 0 or idx >= len(state.last_suggestions):
        return "That number is out of range."
    target, score = state.last_suggestions[idx]
    if score < FUZZY_SUGGEST_THRESHOLD:
        return "That suggestion is too weak to accept."
    state.add_alias(last_raw_input, target)
    return f"Accepted alias: {normalize_topic(last_raw_input)} -> {target}"


def show_why(state: BrainState) -> str:
    if not state.last_why:
        return "No /why info yet."
    return json.dumps(state.last_why, indent=2)


def show_sources(state: BrainState) -> str:
    if state.last_web_sources:
        return "Last web sources:\n" + "\n".join([f"- {u}" for u in state.last_web_sources])
    if state.last_why and state.last_why.get("type") == "exact":
        topic = state.last_why.get("topic", "")
        e = state.knowledge.get(topic, {})
        src = e.get("sources", None)
        if isinstance(src, list) and src:
            return "Sources for last exact answer:\n" + "\n".join([f"- {u}" for u in src])
    return "No sources available yet. Use /weblearn <topic> first."


def main():
    state = BrainState()
    state.run_auto_import()
    state.run_auto_ingest()
    state.start_backup_timer()

    print(f"{APP_NAME} brain online. Type a message, or /help for commands. Ctrl+C to exit.")

    last_user_input = ""

    def shutdown(*_args):
        state._shutdown = True
        state.stop_backup_timer()
        state.save_all()
        print("\nShutting down.")
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
                print(HELP_TEXT)
                continue

            if cmdline.startswith("/teach "):
                rest = cmdline[len("/teach "):].strip()
                topic, answer = parse_pipe_args(rest)
                if not topic or not answer:
                    print("Usage: /teach <topic> | <answer>")
                    continue
                state.set_entry(topic, answer, confidence=0.55, notes="Updated by user re teach")
                print("Saved.")
                continue

            if cmdline.startswith("/teachfile "):
                rest = cmdline[len("/teachfile "):].strip()
                topic, path = parse_pipe_args(rest)
                if not topic or not path:
                    print("Usage: /teachfile <topic> | <path_to_text_file>")
                    continue
                if not os.path.exists(path):
                    print("File not found.")
                    continue
                ans = read_text_file(path).strip()
                if not ans:
                    print("File was empty.")
                    continue
                state.set_entry(topic, ans, confidence=0.55, notes=f"Teachfile from {os.path.basename(path)}")
                print("Saved.")
                continue

            if cmdline.startswith("/import "):
                path = cmdline[len("/import "):].strip()
                if not path:
                    print("Usage: /import <path_to_json_file>")
                    continue
                if not os.path.exists(path):
                    print("File not found.")
                    continue
                count = import_json_file(state, path)
                print(f"Imported {count} entries.")
                continue

            if cmdline.startswith("/importfolder "):
                folder = cmdline[len("/importfolder "):].strip()
                if not folder:
                    print("Usage: /importfolder <folder_path>")
                    continue
                count = import_folder(state, folder)
                print(f"Imported {count} entries.")
                continue

            if cmdline.startswith("/ingest "):
                folder = cmdline[len("/ingest "):].strip()
                if not folder:
                    print("Usage: /ingest <folder_path>")
                    continue
                count = ingest_folder_as_notes(state, folder)
                print(f"Ingested {count} text files.")
                continue

            if cmdline.startswith("/export "):
                folder = cmdline[len("/export "):].strip()
                if not folder:
                    print("Usage: /export <folder_path>")
                    continue
                out = export_knowledge(state, folder)
                print(f"Exported to {out}")
                continue

            if cmdline == "/queue":
                print(show_queue(state))
                continue

            if cmdline == "/clearpending":
                removed = state.clear_pending()
                print(f"Removed {removed} pending items.")
                continue

            if cmdline.startswith("/promote "):
                topic = cmdline[len("/promote "):].strip()
                if not topic:
                    print("Usage: /promote <topic>")
                    continue
                ok = promote_topic(state, topic)
                print("Promoted." if ok else "Topic not found in queue.")
                continue

            if cmdline == "/autoupgrade":
                upgraded, checked = state.autoupgrade_from_notes()
                print(f"Auto upgraded {upgraded} topics from notes. Checked {checked} pending items.")
                continue

            if cmdline.startswith("/weblearn "):
                topic = cmdline[len("/weblearn "):].strip()
                if not topic:
                    print("Usage: /weblearn <topic>")
                    continue
                ok, msg = state.weblearn_topic(topic)
                print(msg if msg else ("Done." if ok else "Failed."))
                continue

            if cmdline == "/webqueue":
                done, attempted = state.webqueue(limit=WEBQUEUE_LIMIT_PER_RUN)
                print(f"Web queue run complete. Learned {done} out of {attempted} attempted (limit {WEBQUEUE_LIMIT_PER_RUN}).")
                continue

            if cmdline == "/sources":
                print(show_sources(state))
                continue

            if cmdline.startswith("/confidence "):
                rest = cmdline[len("/confidence "):].strip()
                parts = rest.split()
                if not parts:
                    print("Usage: /confidence <topic> [new_value_0_to_1]")
                    continue

                topic = " ".join(parts[:-1]) if (len(parts) > 1 and re.match(r"^\d*\.?\d+$", parts[-1])) else rest
                last = parts[-1] if parts else ""

                if len(parts) > 1 and re.match(r"^\d*\.?\d+$", last):
                    val = float(last)
                    ok = set_confidence(state, topic, val)
                    print("Updated." if ok else "Topic not found.")
                else:
                    e = state.get_entry(topic)
                    if not e:
                        print("Topic not found.")
                    else:
                        print(f"{normalize_topic(topic)} confidence={e.get('confidence', 0.0)}")
                continue

            if cmdline.startswith("/lowest"):
                rest = cmdline[len("/lowest"):].strip()
                n = 10
                if rest:
                    try:
                        n = int(rest)
                    except Exception:
                        n = 10
                print(lowest_confidence(state, n))
                continue

            if cmdline.startswith("/alias "):
                rest = cmdline[len("/alias "):].strip()
                a, t = parse_pipe_args(rest)
                if not a or not t:
                    print("Usage: /alias <alias> | <target_topic>")
                    continue
                state.add_alias(a, t)
                print("Alias saved.")
                continue

            if cmdline == "/aliases":
                print(list_aliases(state))
                continue

            if cmdline.startswith("/unalias "):
                a = cmdline[len("/unalias "):].strip()
                if not a:
                    print("Usage: /unalias <alias>")
                    continue
                ok = state.remove_alias(a)
                print("Removed." if ok else "Alias not found.")
                continue

            if cmdline == "/suggest":
                print(show_suggest(state))
                continue

            if cmdline.startswith("/accept "):
                rest = cmdline[len("/accept "):].strip()
                try:
                    num = int(rest)
                except Exception:
                    print("Usage: /accept <number>")
                    continue
                print(accept_suggestion(state, num, last_user_input))
                continue

            if cmdline == "/why":
                print(show_why(state))
                continue

            print("Unknown command. Type /help.")
            continue

        last_user_input = raw
        response = state.answer_query(raw)
        print(f"{APP_NAME}: {response}")


if __name__ == "__main__":
    main()
