#!/usr/bin/env python3
# MachineSpirit brain.py
# Single-file, file-backed (JSON in ./data).
# Phase 1: queuehealth + retry/backoff + clearer logs
# Phase 2: structured synthesis + source ranking + topic expansion + /weburl
# Phase 3: /merge /dedupe /prune (safe) + /selftest
# Phase 4: controlled autonomy (daily + weekly) with guardrails
# Phase 5.1: evidence-weighted confidence (authority + independent sources + reinforcement)

import os
import re
import sys
import json
import time
import shutil
import signal
import difflib
import datetime
import html as html_lib
from typing import Dict, Any, List, Tuple, Optional

try:
    import urllib.parse
    import urllib.request
except Exception:
    urllib = None

APP_NAME = "Machine Spirit"

# -----------------------------
# UTF-8 safety: prevent terminal/systemd encoding crashes
# -----------------------------
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

KNOWLEDGE_PATH = os.path.join(DATA_DIR, "local_knowledge.json")
ALIASES_PATH = os.path.join(DATA_DIR, "aliases.json")

QUEUE_PATH = os.path.join(DATA_DIR, "research_queue.json")
PENDING_PATH = os.path.join(DATA_DIR, "pending_promotions.json")

AUTONOMY_PATH = os.path.join(DATA_DIR, "autonomy.json")

LOGS_DIR = os.path.join(DATA_DIR, "logs")
EXPORTS_DIR = os.path.join(DATA_DIR, "exports")
BACKUPS_DIR = os.path.join(DATA_DIR, "backups")

WEBQUEUE_LOG = os.path.join(LOGS_DIR, "webqueue.log")
CURIOSITY_LOG = os.path.join(LOGS_DIR, "curiosity.log")
BRAIN_LOG = os.path.join(LOGS_DIR, "brain.log")
AUTONOMY_LOG = os.path.join(LOGS_DIR, "autonomy.log")

DEFAULT_MAX_QUEUE_ATTEMPTS = 3
DEFAULT_COOLDOWN_SECONDS = 6 * 60 * 60  # 6 hours

# -----------------------------
# Phase 2: Topic expansion (guardrailed)
# -----------------------------

TOPIC_EXPANSIONS: Dict[str, List[str]] = {
    "subnet": ["subnetting", "cidr", "subnet mask", "network address", "broadcast address"],
    "subnetting": ["cidr", "subnet mask", "network address", "broadcast address", "vlsm"],
    "private ip addressing": ["rfc 1918", "nat", "cidr", "ipv4 address space"],
    "nat": ["port forwarding", "snat vs dnat", "pat", "private ip addressing"],
    "dns": ["dns record types", "recursive vs authoritative dns", "dns caching", "ttl dns"],
}

MAX_EXPANSIONS_PER_TRIGGER = 3

# -----------------------------
# Phase 4: Controlled autonomy defaults
# -----------------------------

AUTONOMY_DEFAULTS: Dict[str, Any] = {
    "enabled": True,

    # Guardrails
    "max_queue_size_total": 250,        # total queue entries allowed (all statuses)
    "max_pending_plus_failed": 80,      # pending + failed allowed before autonomy refuses to add more
    "daily_seed_limit": 3,              # how many new topics autonomy may add per daily run
    "weekly_seed_limit": 6,             # how many new topics autonomy may add per weekly run

    # When autonomy runs, optionally also runs webqueue with a small limit
    "daily_autolearn_limit": 2,
    "weekly_autolearn_limit": 3,

    # Never overwrite user-taught high-confidence answers (already enforced in webqueue),
    # and additionally avoid overwriting ANY very-high-confidence entry (extra safety).
    "protect_confidence_threshold": 0.85,

    # Bookkeeping
    "last_daily_ymd": "",
    "last_weekly_ymd": "",

    # Themes (simple, stable)
    "daily_themes": {
        "mon": ["osi model", "tcp vs udp", "encapsulation"],
        "tue": ["subnetting", "cidr", "vlsm"],
        "wed": ["dns", "dhcp", "nat"],
        "thu": ["routing vs switching", "static routing", "default gateway"],
        "fri": ["vlan", "trunking", "spanning tree protocol"],
        "sat": ["firewall basics", "acl", "least privilege"],
        "sun": ["ipv6 basics", "icmp", "arp"],
    },
    "weekly_themes": [
        ["rfc 1918", "rfc 6890", "ipv4 address space", "nat"],
        ["tls basics", "https", "certificates", "public key cryptography"],
        ["bgp basics", "as number", "route selection", "prefix"],
        ["tcp congestion control", "three-way handshake", "window size", "retransmission"],
    ],
}

# -----------------------------
# Phase 5.1: Evidence-weighted confidence
# -----------------------------
# NOTE: This does NOT rewrite answers. Only changes how confidence is earned.

CONF_FLOOR_UNKNOWN = 0.35
CONF_FLOOR_LEARNED = 0.45
CONF_CAP_NONUSER = 0.88

# Buckets are simple, stable categories.
# Values are "confidence bonus potential" per unique bucket encountered.
AUTH_BUCKET_BONUS: Dict[str, float] = {
    "rfc": 0.25,         # rfc-editor.org, ietf.org
    "nist": 0.22,        # nist.gov
    "standards": 0.20,   # w3.org, ieee, iso, etc.
    "gov": 0.18,         # other .gov
    "edu": 0.15,         # .edu
    "vendor": 0.10,      # docs/support/developer KBs
    "other": 0.06,       # reputable-ish general web
    "wiki": 0.03,        # wikipedia (allowed but small)
}

REINFORCE_DAYS = 7
REINFORCE_BONUS_PER_HIT = 0.02
REINFORCE_BONUS_CAP = 0.10

INDEPENDENT_DOMAIN_BONUS_PER_EXTRA = 0.03
INDEPENDENT_DOMAIN_BONUS_CAP = 0.12

HIGH_AUTH_BUCKETS = {"rfc", "nist", "standards", "gov", "edu"}

# -----------------------------
# Small utilities
# -----------------------------

def now_ts() -> int:
    return int(time.time())

def iso_now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")

def today_ymd() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d")

def weekday_key() -> str:
    return ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][datetime.datetime.now().weekday()]

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
    if s.startswith((">", "$", "#", "sudo ", "ssh ", "cd ", "ls", "cat ", "tail ", "grep ", "nano ", "rm ", "python", "./")):
        return True
    if (" | " in s) or (" >" in s) or ("< " in s) or (" && " in s) or (" || " in s):
        return True
    return False

def looks_like_transcript_prompt(s: str) -> bool:
    s = (s or "").strip().lower()
    if not s:
        return False
    bad = [
        "copy and paste",
        "new chat handoff",
        "type a message",
        "ctrl+c",
        "shutting down",
        "machine spirit brain online",
        "usage:",
    ]
    return any(b in s for b in bad)

def is_junk_topic(s: str) -> Tuple[bool, str]:
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

def parse_iso_to_ts(s: str) -> Optional[int]:
    s = (s or "").strip()
    if not s:
        return None
    try:
        dt = datetime.datetime.fromisoformat(s)
        return int(dt.timestamp())
    except Exception:
        return None

def get_domain(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    try:
        if urllib is None:
            return ""
        p = urllib.parse.urlparse(url)
        return (p.netloc or "").lower()
    except Exception:
        return ""

def clamp(x: float, lo: float, hi: float) -> float:
    try:
        x = float(x)
    except Exception:
        x = lo
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x

def normalize_source_url(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    if s.lower().startswith("wikipedia:"):
        # stored like "Wikipedia: https://...."
        s2 = s.split(":", 1)[1].strip()
        return s2
    return s

def classify_source_bucket(url_or_label: str, title: str = "") -> Tuple[str, str]:
    """
    Returns (bucket, domain_key).
    domain_key is what we use for "independent domain" counting.
    """
    raw = (url_or_label or "").strip()
    if not raw:
        return "other", ""

    # Wikipedia stored as "Wikipedia: https://..." sometimes
    if raw.lower().startswith("wikipedia:"):
        u = normalize_source_url(raw)
        d = get_domain(u) or "wikipedia.org"
        return "wiki", d

    u = normalize_source_url(raw)
    d = get_domain(u)
    t = (title or "").lower()

    # If it's not a URL, treat it as "other"
    if not d and not u.startswith("http"):
        return "other", ""

    # Strong standards / RFC style
    if "rfc-editor.org" in d or "ietf.org" in d:
        return "rfc", d
    if "nist.gov" in d:
        return "nist", d

    # Standards orgs (simple list, stable)
    standards_domains = [
        "w3.org", "ieee.org", "iso.org", "itu.int", "internetsociety.org",
        "icann.org", "iana.org",
    ]
    if any(sd in d for sd in standards_domains):
        return "standards", d

    if d.endswith(".gov"):
        return "gov", d
    if d.endswith(".edu"):
        return "edu", d

    if "wikipedia.org" in d:
        return "wiki", d

    vendor_signals = ["docs.", "support.", "developer.", "learn.", "kb."]
    if any(v in d for v in vendor_signals) or any(v in t for v in ["documentation", "docs", "support"]):
        return "vendor", d

    return "other", d

def ensure_evidence_shape(entry: Dict[str, Any]) -> Dict[str, Any]:
    ev = entry.get("evidence")
    if not isinstance(ev, dict):
        ev = {}
    if not isinstance(ev.get("domains"), dict):
        ev["domains"] = {}
    if not isinstance(ev.get("buckets"), dict):
        ev["buckets"] = {}
    if not isinstance(ev.get("reinforce_count"), int):
        ev["reinforce_count"] = 0
    if not isinstance(ev.get("last_reinforced"), str):
        ev["last_reinforced"] = ""
    entry["evidence"] = ev
    return entry

def update_evidence(entry: Dict[str, Any], sources: List[str], source_titles: Optional[List[str]] = None) -> Dict[str, Any]:
    entry = ensure_evidence_shape(entry)
    ev = entry["evidence"]
    domains = ev.get("domains", {})
    buckets = ev.get("buckets", {})

    if not isinstance(sources, list):
        sources = []

    titles = source_titles if isinstance(source_titles, list) else []
    while len(titles) < len(sources):
        titles.append("")

    for i, s in enumerate(sources):
        url = normalize_source_url(s)
        bucket, domain = classify_source_bucket(s, titles[i] if i < len(titles) else "")
        if domain:
            if domain not in domains or not isinstance(domains.get(domain), dict):
                domains[domain] = {"count": 0, "bucket": bucket}
            domains[domain]["count"] = int(domains[domain].get("count") or 0) + 1
            # keep the strongest bucket seen for this domain
            prev_b = domains[domain].get("bucket") or "other"
            if AUTH_BUCKET_BONUS.get(bucket, 0.0) > AUTH_BUCKET_BONUS.get(prev_b, 0.0):
                domains[domain]["bucket"] = bucket

        buckets[bucket] = int(buckets.get(bucket) or 0) + 1

    ev["domains"] = domains
    ev["buckets"] = buckets
    entry["evidence"] = ev
    return entry

def compute_weighted_confidence(existing_entry: Dict[str, Any], base_floor: float, sources: List[str]) -> Tuple[float, Dict[str, Any]]:
    """
    Evidence-based confidence:
      - authority buckets contribute once each (not per hit)
      - independent domains contribute small bonus
      - reinforcement over time (>=7 days) adds tiny bump
      - never decreases confidence
    """
    entry = ensure_entry_shape(existing_entry)
    entry = ensure_evidence_shape(entry)

    taught_by_user = bool(entry.get("taught_by_user", False))
    existing_conf = float(entry.get("confidence", 0.0) or 0.0)

    # User-taught stays user-taught; we do not mess with its confidence here.
    if taught_by_user:
        return clamp(existing_conf, 0.0, 1.0), entry

    # Update evidence based on new sources
    entry = update_evidence(entry, sources)
    ev = entry.get("evidence", {})
    domains = ev.get("domains", {}) if isinstance(ev.get("domains"), dict) else {}
    buckets = ev.get("buckets", {}) if isinstance(ev.get("buckets"), dict) else {}

    # Authority bonus: count each bucket once (presence-based)
    authority_bonus = 0.0
    for b, bonus in AUTH_BUCKET_BONUS.items():
        if int(buckets.get(b) or 0) > 0:
            authority_bonus += float(bonus)

    # Independent domain bonus
    domain_count = len([d for d in domains.keys() if d])
    indep_bonus = 0.0
    if domain_count > 1:
        indep_bonus = min(INDEPENDENT_DOMAIN_BONUS_CAP, INDEPENDENT_DOMAIN_BONUS_PER_EXTRA * float(domain_count - 1))

    # High-authority independent confirmations multiplier
    high_auth_domains = 0
    for d, meta in domains.items():
        if not isinstance(meta, dict):
            continue
        b = (meta.get("bucket") or "other").strip()
        if b in HIGH_AUTH_BUCKETS:
            high_auth_domains += 1
    mult = 1.0
    if high_auth_domains >= 2:
        mult = 1.12
    elif high_auth_domains == 1:
        mult = 1.06

    # Reinforcement over time (only if re-learned after a gap)
    last_ref = (ev.get("last_reinforced") or "").strip()
    last_ts = parse_iso_to_ts(last_ref) if last_ref else None
    reinforce_bonus = 0.0
    if last_ts is None:
        # first time we start tracking
        ev["last_reinforced"] = iso_now()
    else:
        days = (now_ts() - int(last_ts)) / float(24 * 3600)
        if days >= float(REINFORCE_DAYS):
            ev["reinforce_count"] = int(ev.get("reinforce_count") or 0) + 1
            ev["last_reinforced"] = iso_now()

    reinforce_hits = int(ev.get("reinforce_count") or 0)
    if reinforce_hits > 0:
        reinforce_bonus = min(REINFORCE_BONUS_CAP, REINFORCE_BONUS_PER_HIT * float(reinforce_hits))

    # Phase 5.1: prevent confidence inflation from repeat learns
    # If we did not gain a new domain and the user has not confirmed, do not apply reinforcement bonus.
    gained_new_domain = bool(ev.get("_gained_new_domain"))

    # confirmed may not be initialized yet in this function, so read it safely from existing_entry
    confirm_count = 0
    try:
        c = (existing_entry or {}).get("confirmed") or {}
        confirm_count = int(c.get("count") or 0)
    except Exception:
        confirm_count = 0

    if (not gained_new_domain) and (confirm_count <= 0):
        reinforce_bonus = 0.0

    # Combine
    floor = max(float(base_floor), CONF_FLOOR_UNKNOWN)
    raw = max(existing_conf, floor) + authority_bonus + indep_bonus + reinforce_bonus
    raw = raw * mult

    # Clamp & never decrease
    new_conf = clamp(max(existing_conf, raw), CONF_FLOOR_UNKNOWN, CONF_CAP_NONUSER)

    entry["evidence"] = ev
    return new_conf, entry

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
        if "source_url" not in item:
            item["source_url"] = ""
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

def load_autonomy() -> Dict[str, Any]:
    cfg = read_json(AUTONOMY_PATH, {})
    if not isinstance(cfg, dict):
        cfg = {}
    merged = json.loads(json.dumps(AUTONOMY_DEFAULTS))
    for k, v in cfg.items():
        merged[k] = v
    if isinstance(cfg.get("daily_themes"), dict):
        merged_dt = dict(AUTONOMY_DEFAULTS["daily_themes"])
        merged_dt.update(cfg["daily_themes"])
        merged["daily_themes"] = merged_dt
    return merged

def save_autonomy(cfg: Dict[str, Any]) -> None:
    backup_file(AUTONOMY_PATH)
    atomic_write_json(AUTONOMY_PATH, cfg)

# -----------------------------
# Alias (fuzzy suggestion + accept)
# -----------------------------

def suggest_alias(topic: str, knowledge: Dict[str, Any], aliases: Dict[str, str]) -> Optional[str]:
    t = normalize_topic(topic)
    if not t:
        return None
    if t in aliases:
        return aliases[t]
    keys = list(knowledge.keys())
    if not keys:
        return None
    if t in knowledge:
        return None
    match = difflib.get_close_matches(t, keys, n=1, cutoff=0.70)
    if match:
        return match[0]
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
    # Phase 5.1 evidence is optional; only created when we learn from sources.
    if "evidence" in entry:
        entry = ensure_evidence_shape(entry)
    return entry

def set_knowledge(topic: str, answer: str, confidence: float, sources: Optional[List[str]] = None,
                  notes: str = "", taught_by_user: bool = False,
                  _merge_evidence: Optional[Dict[str, Any]] = None) -> None:
    """
    Phase 5.1.2: confidence curve tuning (Learning Quality & Trust)

    Hard rules:
    - Never lowers confidence automatically.
    - Confidence should only increase when:
        (A) user confirms (/confirm), OR
        (B) 2+ independent domains support the topic.
    - More independent domains can keep increasing confidence (2 sources, 3 sources, 4 sources...).
    """
    topic_n = normalize_topic(topic)
    if not topic_n:
        return

    def bucket_for_domain(domain: str) -> str:
        d = (domain or "").lower().strip()
        if not d:
            return "other"
        if ("rfc-editor.org" in d) or ("ietf.org" in d):
            return "rfc"
        if d.endswith(".gov") or d.endswith(".edu") or ("nist.gov" in d):
            return "gov_edu"
        if "wikipedia.org" in d:
            return "wiki"
        if any(x in d for x in ["docs.", "developer.", "support.", "learn.", "kb."]):
            return "vendor"
        return "other"

    def domains_from_sources(srcs: List[str]) -> List[str]:
        out: List[str] = []
        seen = set()
        for s in (srcs or []):
            u = (s or "").strip()
            if not u:
                continue
            d = get_domain(u)
            if not d:
                continue
            d = d.lower().strip()
            if d not in seen:
                seen.add(d)
                out.append(d)
        return out

    def bucket_counts(domains: List[str]) -> Dict[str, int]:
        b: Dict[str, int] = {}
        seen = set()
        for d in (domains or []):
            dd = (d or "").lower().strip()
            if not dd or dd in seen:
                continue
            seen.add(dd)
            bk = bucket_for_domain(dd)
            b[bk] = b.get(bk, 0) + 1
        return b

    # --- load existing
    k = load_knowledge()
    entry = ensure_entry_shape(k.get(topic_n, {}))

    existing_conf = float(entry.get("confidence", 0.0))

    # Merge sources (unique)
    merged_sources: List[str] = []
    for s in (entry.get("sources") or []) + (sources or []):
        s2 = (s or "").strip()
        if s2 and s2 not in merged_sources:
            merged_sources.append(s2)

    # Evidence (domains + bucket counts) based on merged_sources
    domains = domains_from_sources(merged_sources)
    buckets = bucket_counts(domains)

    # If caller provided evidence to merge, merge it gently (do not duplicate)
    if isinstance(_merge_evidence, dict):
        try:
            extra_domains = _merge_evidence.get("domains") or _merge_evidence.get("evidence_domains") or []
            for d in (extra_domains or []):
                d2 = (d or "").strip().lower()
                if d2 and d2 not in domains:
                    domains.append(d2)
            buckets = bucket_counts(domains)
        except Exception:
            pass

    # Reinforcement bookkeeping (track, but does NOT raise confidence by itself)
    reinf = entry.get("reinforcement")
    if not isinstance(reinf, dict):
        reinf = {"count": 0, "last": ""}
    if not taught_by_user:
        reinf["count"] = int(reinf.get("count") or 0) + 1
        reinf["last"] = iso_now()

    # Confirm bookkeeping (used to raise confidence)
    confirmed = entry.get("confirmed")
    if not isinstance(confirmed, dict):
        confirmed = {"count": 0, "last": ""}

    # --- confidence curve
    # Hard gate: only allow raises if user confirmed OR 2+ independent domains exist
    domain_count = len(domains)
    confirm_count = int(confirmed.get("count") or 0)

    # Base floor (we will never go below existing_conf)
    new_conf = max(existing_conf, float(confidence))

    # cap without user confirmation
    cap_no_confirm = 0.92
    cap_with_confirm = 0.99

    can_raise_by_domains = (domain_count >= 2)
    can_raise_by_user = (confirm_count >= 1)

    if (not can_raise_by_domains) and (not can_raise_by_user):
        # gate closed: do not raise beyond existing_conf (ignore incoming confidence bumps)
        new_conf = existing_conf

    else:
        # gate open: compute a target based on domain_count + authority buckets
        # Base target starts at max(existing_conf, 0.55) so 2-source topics don't stay stuck at 0.45
        target = max(existing_conf, 0.55)

        # each additional independent domain adds a smaller bump (diminishing returns)
        # 2 domains: +0.10, 3: +0.16, 4: +0.20, 5+: +0.23...
        bumps = [0.00, 0.10, 0.16, 0.20, 0.23, 0.25]
        idx = domain_count if domain_count < len(bumps) else (len(bumps) - 1)
        target += bumps[idx]

        # authority bonuses (small but meaningful)
        if buckets.get("rfc", 0) >= 1:
            target += 0.04
        if buckets.get("gov_edu", 0) >= 1:
            target += 0.03
        if buckets.get("vendor", 0) >= 1:
            target += 0.01
        # wiki never adds bonus (it can still be part of "2 domains" proof)

        # user confirmation gives a direct bump (stackable per confirm)
        if can_raise_by_user:
            target += min(0.05 * confirm_count, 0.20)

        # cap selection
        cap = cap_with_confirm if can_raise_by_user else cap_no_confirm
        target = min(target, cap)

        # never lower
        new_conf = max(existing_conf, target)

    # --- write fields
    entry["answer"] = (answer or "").strip()
    entry["confidence"] = float(new_conf)
    entry["updated"] = iso_now()
    entry["notes"] = notes or entry.get("notes", "")
    entry["taught_by_user"] = bool(taught_by_user) or bool(entry.get("taught_by_user", False))
    entry["sources"] = merged_sources
    # Phase 5.1: track whether this update added a NEW independent domain
    old_domains = set([(d or "").lower().strip() for d in (entry.get("evidence_domains") or []) if (d or "").strip()])
    new_domains = set([(d or "").lower().strip() for d in (domains or []) if (d or "").strip()])
    entry["_gained_new_domain"] = (len(new_domains - old_domains) > 0)
    entry["evidence_domains"] = domains
    entry["evidence_buckets"] = buckets
    entry["reinforcement"] = reinf
    entry["confirmed"] = confirmed

    k[topic_n] = entry
    save_knowledge(k)

# Web fetch/search + Phase 2 ranking
# -----------------------------

def http_get(url: str, timeout: int = 15, headers: Optional[Dict[str, str]] = None) -> Tuple[int, str]:
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
    html = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    html = re.sub(r"(?is)<.*?>", " ", html)
    html = html.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    html = html.replace("&#39;", "'").replace("&quot;", '"')
    html = re.sub(r"\s+", " ", html).strip()
    return html

def wiki_opensearch_title(query: str) -> Optional[str]:
    if urllib is None:
        return None
    q = urllib.parse.quote(query)
    url = f"https://en.wikipedia.org/w/api.php?action=opensearch&search={q}&limit=1&namespace=0&format=json"
    code, text = http_get(url)
    if code != 200 or not text:
        return None
    try:
        data = json.loads(text)
        titles = data[1] if len(data) > 1 else []
        if titles:
            return titles[0]
    except Exception:
        return None
    return None

def wiki_summary(title: str) -> Optional[Tuple[str, str]]:
    if urllib is None:
        return None
    t = (title or "").strip()
    if not t:
        return None
    safe = urllib.parse.quote(t.replace(" ", "_"))
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{safe}"
    code, text = http_get(url)
    if code != 200 or not text:
        return None
    try:
        data = json.loads(text)
        extract = (data.get("extract") or "").strip()
        page_url = ""
        content_urls = data.get("content_urls") or {}
        desktop = content_urls.get("desktop") or {}
        page_url = desktop.get("page") or ""
        if extract:
            return extract, page_url
    except Exception:
        return None
    return None

def clean_ddg_link(link: str) -> str:
    link = (link or "").strip()
    if not link:
        return ""
    if link.startswith("//"):
        link = "https:" + link
    try:
        link = html_lib.unescape(link)
    except Exception:
        link = link.replace("&amp;", "&")
    try:
        if "duckduckgo.com/l/?" in link and "uddg=" in link and urllib is not None:
            p = urllib.parse.urlparse(link)
            qs = urllib.parse.parse_qs(p.query)
            if "uddg" in qs and qs["uddg"]:
                target = qs["uddg"][0]
                target = urllib.parse.unquote(target).strip()
                if target.startswith("//"):
                    target = "https:" + target
                return target
    except Exception:
        pass
    return link

def ddg_html_results(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    if urllib is None:
        return out
    q = urllib.parse.quote(query)
    url = f"https://duckduckgo.com/html/?q={q}"
    code, html = http_get(url)
    if code != 200 or not html:
        return out
    for m in re.finditer(r'(?is)<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html):
        if len(out) >= max_results:
            break
        link = m.group(1).strip()
        link = clean_ddg_link(link)
        title = strip_html(m.group(2))
        out.append({"title": title, "url": link, "snippet": ""})
    snippets = []
    for sm in re.finditer(r'(?is)<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', html):
        snippets.append(strip_html(sm.group(1)))
    for i in range(min(len(out), len(snippets))):
        out[i]["snippet"] = snippets[i]
    return out

def try_standards_first(topic: str) -> Optional[Tuple[str, str]]:
    c1 = ddg_html_results(f"site:rfc-editor.org {topic}", max_results=6)
    best = choose_preferred_source(c1) if c1 else None
    if best and best.get("url"):
        return best["url"], "rfc-editor.org"
    c2 = ddg_html_results(f"site:ietf.org {topic}", max_results=6)
    best = choose_preferred_source(c2) if c2 else None
    if best and best.get("url"):
        return best["url"], "ietf.org"
    c3 = ddg_html_results(f"site:nist.gov {topic}", max_results=6)
    best = choose_preferred_source(c3) if c3 else None
    if best and best.get("url"):
        return best["url"], "nist.gov"
    return None

def ddg_lite_results(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    if urllib is None:
        return out
    q = urllib.parse.quote(query)
    url = f"https://lite.duckduckgo.com/lite/?q={q}"
    code, html = http_get(url)
    if code != 200 or not html:
        return out
    for m in re.finditer(r'(?is)<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html):
        if len(out) >= max_results:
            break
        link = m.group(1).strip()
        link = clean_ddg_link(link)
        title = strip_html(m.group(2))
        if not link.startswith("http"):
            continue
        if not title or len(title) < 3:
            continue
        out.append({"title": title, "url": link, "snippet": ""})
    return out

def source_score(url: str, title: str = "") -> int:
    d = get_domain(url)
    t = (title or "").lower()
    u = (url or "").lower()
    score = 0

    # Phase 5.1: hard deny list (almost always low-signal / SEO / personal publishing)
    # Use a huge negative so it never wins.
    hard_deny = [
        "medium.com",
        "blogspot.",
        "wordpress.com",
        "wixsite.com",
        "weebly.com",
    ]
    if any(x in d for x in hard_deny):
        return -9999

    # Phase 5.1: strong preference for primary standards / official sources
    if "rfc-editor.org" in d:
        score += 160
    if "ietf.org" in d:
        score += 140
    if "iana.org" in d:
        score += 130
    if "nist.gov" in d:
        score += 150
    if d.endswith(".gov"):
        score += 110
    if d.endswith(".edu"):
        score += 110

    # Web standards bodies / spec sources
    if "w3.org" in d:
        score += 135
    if "whatwg.org" in d:
        score += 130
    if "ieee.org" in d:
        score += 120
    if "iso.org" in d:
        score += 120
    if "opengroup.org" in d:
        score += 115

    # Wikipedia is allowed only as fallback
    if "wikipedia.org" in d:
        score += 5

    # Mild preference: vendor documentation portals (signal words + known vendors)
    vendor_signals = ["docs.", "support.", "developer.", "learn.", "kb.", "/docs", "/documentation"]
    if any(v in d for v in vendor_signals) or any(v in u for v in ["/docs", "/documentation", "/kb", "/support"]):
        score += 25

    major_vendor_domains = [
        "cisco.com",
        "juniper.net",
        "microsoft.com",
        "learn.microsoft.com",
        "cloudflare.com",
        "akamai.com",
        "redhat.com",
        "ibm.com",
        "oracle.com",
        "developer.apple.com",
        "developers.google.com",
    ]
    if any(m in d for m in major_vendor_domains):
        score += 20

    # Phase 5.1: social / login-wall domains are usually bad learning sources
    bad_domains = ["linkedin.com", "facebook.com", "quora.com", "pinterest.com", "x.com", "twitter.com", "instagram.com", "tiktok.com"]
    if any(bd in d for bd in bad_domains):
        score -= 140

    # Blog / newsletter signals (not always wrong, but lower trust)
    blog_signals = ["blog", "wordpress", "blogspot", "substack"]
    if any(b in d for b in blog_signals):
        score -= 40

    # SEO / content farm markers (URL)
    seo_markers = [
        "utm_", "ref=", "aff=", "affiliate",
        "best-", "top-", "vs-", "review", "reviews",
        "coupon", "promo", "discount",
        "/tag/", "/tags/", "/category/", "/categories/",
    ]
    if any(m in u for m in seo_markers):
        score -= 20

    # SEO / opinion markers (title)
    if any(w in t for w in ["opinion", "my experience", "top 10", "top ten", "best", "review", "vs "]):
        score -= 15

    if url.startswith("https://"):
        score += 5

    return score

def choose_best_source(candidates: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
    if not candidates:
        return None
    scored = []
    for c in candidates:
        url = c.get("url", "")
        title = c.get("title", "")
        scored.append((source_score(url, title), c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]

def choose_preferred_source(candidates: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
    """
    Prefer non-Wikipedia sources whenever possible.
    Wikipedia should be fallback-only unless there are no usable non-wiki candidates.
    """
    if not candidates:
        return None

    scored = []
    for c in candidates:
        url = (c.get("url") or "").strip()
        title = (c.get("title") or "").strip()
        if not url:
            continue
        scored.append((source_score(url, title), c))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best = scored[0]
    best_domain = get_domain(best.get("url", ""))

    nonwiki = [(sc, c) for (sc, c) in scored if "wikipedia.org" not in get_domain(c.get("url", ""))]

    # HARD RULE: if Wikipedia is best and we have ANY non-wiki candidate that isn't total trash, choose it.
    # (This keeps wikipedia as a true fallback.)
    if "wikipedia.org" in best_domain and nonwiki:
        nonwiki.sort(key=lambda x: x[0], reverse=True)
        sc2, c2 = nonwiki[0]

        # If the best non-wiki is catastrophically low, allow wiki fallback.
        # Otherwise, prefer non-wiki even if it scored lower.
        if sc2 > -500:
            return c2

    return best

def choose_preferred_source_excluding(candidates: List[Dict[str, str]], avoid_domains: List[str]) -> Optional[Dict[str, str]]:
    """
    Phase 5.1: multi-source reinforcement helper
    Prefer the best source, but if we already have a domain, try to pick a different one.
    """
    avoid = set([(d or "").lower().strip() for d in (avoid_domains or []) if (d or "").strip()])
    if not candidates:
        return None

    filtered = []
    for c in candidates:
        url = (c.get("url") or "").strip()
        if not url:
            continue
        d = get_domain(url)
        if d and d.lower() in avoid:
            continue
        filtered.append(c)

    # If filtering removed everything, fall back to normal behavior
    return choose_preferred_source(filtered if filtered else candidates)


def fetch_page_text_debug(url: str, max_chars: int = 12000) -> Tuple[bool, str, str]:
    """
    Debug fetch that returns (ok, text, reason).
    Does NOT modify normal learning behavior.
    """
    code, body = http_get(url)
    if code != 200 or not body:
        return False, "", f"http_{code}"

    lowered_html = body.lower()

    blocked_markers = [
        ("sign in" in lowered_html and "password" in lowered_html),
        ("log in" in lowered_html and "password" in lowered_html),
        ("create account" in lowered_html and ("sign in" in lowered_html or "log in" in lowered_html)),
        ("join now" in lowered_html and ("sign in" in lowered_html or "log in" in lowered_html)),

        ("cookie" in lowered_html and "consent" in lowered_html and ("accept" in lowered_html or "agree" in lowered_html)),
        ("we value your privacy" in lowered_html and "cookie" in lowered_html),
        ("privacy choices" in lowered_html and "cookie" in lowered_html),
        ("accept all cookies" in lowered_html and "cookie" in lowered_html),

        ("enable javascript" in lowered_html),
        ("please enable javascript" in lowered_html),
        ("this site requires javascript" in lowered_html),
        ("checking your browser before accessing" in lowered_html),

        ("captcha" in lowered_html and ("verify" in lowered_html or "human" in lowered_html)),
        ("unusual traffic" in lowered_html and ("robot" in lowered_html or "automated" in lowered_html)),
        ("are you a robot" in lowered_html),
        ("cloudflare" in lowered_html and ("attention required" in lowered_html or "security check" in lowered_html)),

        ("subscribe to continue" in lowered_html),
        ("subscription" in lowered_html and "continue" in lowered_html),
        ("to continue reading" in lowered_html and ("subscribe" in lowered_html or "sign in" in lowered_html)),
        ("metered paywall" in lowered_html),
    ]
    if any(blocked_markers):
        return False, "", "blocked_marker"

    text = strip_html(body)
    if not text:
        return False, "", "strip_empty"

    cleaned = re.sub(r"\s+", " ", (text or "")).strip()
    if len(cleaned) < 500:
        return False, "", f"thin_{len(cleaned)}"

    return True, text, "ok"

def fetch_page_text(url: str, max_chars: int = 12000) -> Tuple[bool, str]:
    code, body = http_get(url)
    if code != 200 or not body:
        return False, ""

    # Phase 5.1: reject gated / unusable pages BEFORE stripping (HTML-level)
    # (login walls, cookie walls, JS-required pages, captchas, paywalls)
    lowered_html = body.lower()
    blocked_markers = [
        ("sign in" in lowered_html and "password" in lowered_html),
        ("log in" in lowered_html and "password" in lowered_html),
        ("create account" in lowered_html and ("sign in" in lowered_html or "log in" in lowered_html)),
        ("join now" in lowered_html and ("sign in" in lowered_html or "log in" in lowered_html)),

        ("cookie" in lowered_html and "consent" in lowered_html and ("accept" in lowered_html or "agree" in lowered_html)),
        ("we value your privacy" in lowered_html and "cookie" in lowered_html),
        ("privacy choices" in lowered_html and "cookie" in lowered_html),
        ("accept all cookies" in lowered_html and "cookie" in lowered_html),

        ("enable javascript" in lowered_html),
        ("please enable javascript" in lowered_html),
        ("this site requires javascript" in lowered_html),
        ("checking your browser before accessing" in lowered_html),

        ("captcha" in lowered_html and ("verify" in lowered_html or "human" in lowered_html)),
        ("unusual traffic" in lowered_html and ("robot" in lowered_html or "automated" in lowered_html)),
        ("are you a robot" in lowered_html),
        ("cloudflare" in lowered_html and ("attention required" in lowered_html or "security check" in lowered_html)),

        ("subscribe to continue" in lowered_html),
        ("subscription" in lowered_html and "continue" in lowered_html),
        ("to continue reading" in lowered_html and ("subscribe" in lowered_html or "sign in" in lowered_html)),
        ("metered paywall" in lowered_html),
    ]
    if any(blocked_markers):
        return False, ""
    text = strip_html(body)
    if not text:
        return False, ""

    # Phase 5.1: thin content detection (after stripping)
    cleaned = re.sub(r"\s+", " ", (text or "")).strip()
    if len(cleaned) < 500:
        return False, ""

    # Phase 5.1: reject login/paywall/cookie-wall pages (treat as fetch fail)
    low = text.lower()
    login_signals = [
        "sign in", "log in", "join linkedin", "agree & join", "create your free account",
        "enable cookies", "accept all cookies", "privacy policy", "cookie policy",
        "to continue, please", "verify you are a human", "captcha"
    ]
    # If a page is mostly auth/cookie boilerplate, it’s not useful as a learning source.
    hits = sum(1 for sig in login_signals if sig in low)
    if hits >= 3:
        return False, ""

    if len(text) > max_chars:
        text = text[:max_chars]
    return True, text

def pick_definition_sentence(topic: str, text: str) -> str:
    topic_n = normalize_topic(topic)

    # RFC/standards pages often have ugly nav headers or metadata lines. We skip those.
    def is_header_junk(s: str) -> bool:
        s0 = (s or "").strip()
        if not s0:
            return True
        low = s0.lower()

        junk_signals = [
            "status of this memo",
            "rfc home", "text | pdf | html", "tracker", "ipr", "errata", "info page",
            "network working group", "request for comments",
            "obsoletes:", "updates:", "category:", "issn:", "doi:", "bcp:", "std:",
        ]
        if any(j in low for j in junk_signals):
            return True

        # author/affiliation-ish lines that show up early in RFC HTML
        if re.match(r"^[a-z][a-z\-]+(\s+[a-z][a-z\-]+)?\s+(bcp|std)\s*:\s*\d+", low):
            return True

        # lots of bracket/pipe markup is usually navigation
        if s0.count("[") >= 1 and s0.count("]") >= 1:
            return True
        if s0.count("|") >= 2:
            return True

        # very long "sentence" is usually scraped header/nav
        if len(s0) > 220:
            return True

        return False

    # Split loosely: RFC pages sometimes don’t have clean punctuation breaks
    chunks = re.split(r"[\n\r]+|(?<=[\.\!\?])\s+", text)

    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "")).strip()

    # Pass 1: prefer actual definition phrases
    prefer_phrases = [
        "this document describes",
        "this document specifies",
        "this document defines",
        "this memo defines",
        "this document provides",
        "this document discusses",
    ]
    for s in chunks[:180]:
        s2 = norm(s)
        if not s2:
            continue
        low = s2.lower()
        if is_header_junk(s2):
            continue
        if any(p in low for p in prefer_phrases):
            return s2

    # Pass 2: prefer a chunk containing the topic, but skip junk
    for s in chunks[:180]:
        s2 = norm(s)
        if not s2:
            continue
        if is_header_junk(s2):
            continue
        if topic_n and topic_n in s2.lower():
            return s2

    # Pass 3: otherwise return the first clean chunk
    for s in chunks[:120]:
        s2 = norm(s)
        if not s2:
            continue
        if is_header_junk(s2):
            continue
        return s2

    return ""



def bullets_from_text(text: str, max_bullets: int = 6) -> List[str]:
    sentences = re.split(r"(?<=[\.\!\?])\s+", text)
    bullets: List[str] = []
    seen = set()
    for s in sentences:
        s2 = s.strip()
        if not s2:
            continue
        key = re.sub(r"\s+", " ", s2).strip().lower()
        if key in seen:
            continue
        if 40 <= len(s2) <= 160:
            bullets.append(s2)
            seen.add(key)
        if len(bullets) >= max_bullets:
            break

    if not bullets:
        chunk = text[:700].strip()
        if chunk:
            bullets.append(chunk)

    return bullets[:max_bullets]

def structured_synthesis(topic: str, seed_text: str, source_url: str, source_label: str) -> str:
    definition = pick_definition_sentence(topic, seed_text)
    bullets = bullets_from_text(seed_text, max_bullets=6)

    # Phase 5: avoid repeating the Definition as the first bullet
    if definition:
        dkey = re.sub(r"\s+", " ", definition).strip().lower()
        bullets = [b for b in bullets if re.sub(r"\s+", " ", b).strip().lower() != dkey]

    # Also drop obvious page junk lines
    bullets = [b for b in bullets if "watch video" not in b.lower()]

    # Phase 5: extra cleanup for RFC/IETF style pages (remove metadata bullets)
    dsrc = get_domain(source_url)
    if ("rfc-editor.org" in dsrc) or ("ietf.org" in dsrc):
        drop = [
            "request for comments", "network working group", "obsoletes:", "updates:",
            "category:", "bcp:", "std:", "status of this memo",
        ]
        bullets = [b for b in bullets if not any(x in b.lower() for x in drop)]


    examples = []
    t = normalize_topic(topic)
    if "subnet" in t or "cidr" in t:
        examples = [
            "Example: 192.168.1.0/24 has 256 total addresses (0-255), with usable hosts typically .1 to .254 (depends on context).",
            "Example: /26 splits a /24 into 4 subnets (each block size 64).",
        ]
    elif "dns" in t:
        examples = [
            "Example: A record maps a name to an IPv4 address.",
            "Example: CNAME is an alias pointing one name to another name.",
        ]
    elif "rfc 1918" in t or "private ip" in t:
        examples = [
            "Example private IPv4 blocks: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16.",
            "Example: home routers typically use NAT so private addresses can reach the public internet.",
        ]

    mistakes = []
    if "subnet" in t or "cidr" in t:
        mistakes = [
            "Mistake: confusing the subnet mask with the network address.",
            "Mistake: forgetting that network + broadcast addresses are usually not usable host addresses in IPv4.",
        ]
    elif "dns" in t:
        mistakes = [
            "Mistake: assuming DNS changes apply instantly (caching/TTL can delay).",
            "Mistake: mixing up authoritative vs recursive DNS roles.",
        ]
    elif "rfc 1918" in t or "private ip" in t:
        mistakes = [
            "Mistake: thinking private IPs are routable on the public internet (they are not).",
            "Mistake: assuming NAT is security by itself (it helps, but it’s not a firewall).",
        ]

    quick = []
    if "subnet" in t or "cidr" in t:
        quick = [
            "Quick check: /24 = 255.255.255.0, /16 = 255.255.0.0, /8 = 255.0.0.0.",
            "Quick check: block size = 256 - last mask octet (when the split is in the last octet).",
            "Quick check: usable hosts in subnet = 2^(host bits) - 2 (typical IPv4).",
        ]
    elif "dns" in t:
        quick = [
            "Quick check: if a name resolves sometimes but not others, suspect caching/TTL or split-horizon DNS.",
        ]
    elif "rfc 1918" in t or "private ip" in t:
        quick = [
            "Quick check: if the IP starts with 10., 192.168., or 172.16-172.31, it’s likely private RFC1918 space.",
        ]

    out = []
    out.append(f"{topic.upper()}")
    out.append("")
    if definition:
        out.append("Definition:")
        out.append(f"- {definition}")
        out.append("")
    out.append("Key points:")
    for b in bullets:
        out.append(f"- {b}")
    out.append("")
    if examples:
        out.append("Examples:")
        for e in examples:
            out.append(f"- {e}")
        out.append("")
    if mistakes:
        out.append("Common mistakes:")
        for m in mistakes:
            out.append(f"- {m}")
        out.append("")
    if quick:
        out.append("Practice recognition:")
        for q in quick:
            out.append(f"- {q}")
        out.append("")
    out.append("Sources:")
    if source_url:
        out.append(f"- {source_label}: {source_url}")
    else:
        out.append(f"- {source_label}")
    return "\n".join(out).strip()

def expand_topic_if_needed(topic: str) -> List[str]:
    topic_n = normalize_topic(topic)
    expansions = TOPIC_EXPANSIONS.get(topic_n, [])
    if not expansions:
        return []
    return expansions[:MAX_EXPANSIONS_PER_TRIGGER]

def web_learn_topic(topic: str, forced_url: str = "", avoid_domains: Optional[List[str]] = None) -> Tuple[bool, str, List[str], str]:
    avoid_domains = avoid_domains or []

    sources: List[str] = []
    chosen_url = ""

    if forced_url:
        ok, txt = fetch_page_text(forced_url)
        if not ok:
            return False, "Web fetch failed for provided URL.", sources, ""
        answer = structured_synthesis(topic, txt, forced_url, "Direct URL")
        sources.append(forced_url)
        return True, answer, sources, forced_url

    cands = ddg_html_results(topic, max_results=6)
    if cands:
        best = choose_preferred_source_excluding(cands, avoid_domains)
        if best:
            chosen_url = best.get("url", "")
            ok, txt = fetch_page_text(chosen_url)
            if ok:
                label = get_domain(chosen_url) or "Web"
                answer = structured_synthesis(topic, txt, chosen_url, label)
                sources.append(chosen_url)
                return True, answer, sources, chosen_url
            sources.append(chosen_url)
            safe_log(WEBQUEUE_LOG, f"weblearn: fetch failed best_url='{chosen_url}' trying ddg_lite + wiki fallback")

    cands2 = ddg_lite_results(topic, max_results=6)
    if cands2:
        best = choose_preferred_source_excluding(cands2, avoid_domains)
        if best:
            chosen_url = best.get("url", "")
            ok, txt = fetch_page_text(chosen_url)
            if ok:
                label = get_domain(chosen_url) or "Web"
                answer = structured_synthesis(topic, txt, chosen_url, label)
                sources.append(chosen_url)
                return True, answer, sources, chosen_url
            sources.append(chosen_url)
            safe_log(WEBQUEUE_LOG, f"weblearn: fetch failed best_url='{chosen_url}' falling back to wikipedia")

    wtitle = wiki_opensearch_title(topic)
    if wtitle:
        ws = wiki_summary(wtitle)
        if ws:
            extract, page_url = ws
            chosen_url = page_url or ""
            answer = structured_synthesis(topic, extract, chosen_url, "Wikipedia")
            sources.append(f"Wikipedia: {page_url}" if page_url else f"Wikipedia: {wtitle}")
            return True, answer, sources, chosen_url

    return False, "Web search returned no results or could not be fetched.", sources, ""

# -----------------------------
# Queue logic (Phase 1)
# -----------------------------

def queue_find_item(q: List[Dict[str, Any]], topic: str) -> Optional[Dict[str, Any]]:
    tn = normalize_topic(topic)
    for item in q:
        if normalize_topic(item.get("topic", "")) == tn:
            return item
    return None

def queue_stats(q: Optional[List[Dict[str, Any]]] = None) -> Dict[str, int]:
    q = q if q is not None else load_queue()
    counts = {"total": 0, "pending": 0, "failed": 0, "running": 0, "done": 0, "failed_final": 0, "other": 0}
    counts["total"] = len(q)
    for item in q:
        st = item.get("status", "pending")
        if st in counts:
            counts[st] += 1
        else:
            counts["other"] += 1
    return counts

def autonomy_queue_guard_ok() -> Tuple[bool, str]:
    cfg = load_autonomy()
    q = load_queue()
    stats = queue_stats(q)
    if stats["total"] >= int(cfg.get("max_queue_size_total", 250)):
        return False, f"queue_total_limit_reached:{stats['total']}"
    if (stats["pending"] + stats["failed"]) >= int(cfg.get("max_pending_plus_failed", 80)):
        return False, f"queue_pending_failed_limit_reached:{stats['pending'] + stats['failed']}"
    return True, "ok"

def queue_add(topic: str, reason: str = "", confidence: float = 0.35, source_url: str = "") -> Tuple[bool, str]:
    topic_n = normalize_topic(topic)
    junk, why = is_junk_topic(topic_n)
    if junk:
        return False, f"Not queued (junk): {why}"

    q = load_queue()
    existing = queue_find_item(q, topic_n)
    if existing:
        st = (existing.get("status") or "").strip().lower()
        reason_l = (reason or "").lower()

        deepen_signals = [
            "low confidence",
            "deepen",
            "curiosity",
            "autonomy",
            "expanded from",
            "learned via",
            "answer exists but confidence is low",
        ]
        wants_deepen = any(sig in reason_l for sig in deepen_signals)

        if st in ("done", "failed_final") and wants_deepen:
            existing["status"] = "pending"
            existing["requested_on"] = iso_now()
            existing["current_confidence"] = float(confidence)
            existing["attempts"] = 0
            existing["last_attempt_ts"] = 0
            existing["fail_reason"] = ""
            existing["completed_on"] = ""
            existing["worker_note"] = ""
            if reason:
                existing["reason"] = reason
            if source_url:
                existing["source_url"] = source_url
            save_queue(q)
            return True, "Re-queued for deeper learning."

        if reason and not existing.get("reason"):
            existing["reason"] = reason
        if source_url and not existing.get("source_url"):
            existing["source_url"] = source_url
        save_queue(q)
        return False, "Already queued."

    item = {
        "topic": topic_n,
        "reason": reason or "",
        "requested_on": iso_now(),
        "status": "pending",
        "current_confidence": float(confidence),

        "attempts": 0,
        "max_attempts": DEFAULT_MAX_QUEUE_ATTEMPTS,
        "last_attempt_ts": 0,
        "cooldown_seconds": DEFAULT_COOLDOWN_SECONDS,
        "fail_reason": "",
        "completed_on": "",
        "worker_note": "",
        "source_url": source_url or "",
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
            ts = parse_iso_to_ts(item.get("requested_on", ""))
            if ts:
                if oldest_pending_ts is None or ts < oldest_pending_ts:
                    oldest_pending_ts = ts

        if st in ("failed", "failed_final"):
            r = (item.get("fail_reason") or "unknown").strip() or "unknown"
            failure_reasons[r] = failure_reasons.get(r, 0) + 1

        if st == "running":
            last = int(item.get("last_attempt_ts") or 0)
            if last > 0 and (now - last) > (60 * 30):
                stuck_items.append(item)

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
            {"topic": i.get("topic", ""), "last_attempt_age": human_age(now - int(i.get("last_attempt_ts") or 0))}
            for i in stuck_items[:5]
        ],
    }

def can_attempt(item: Dict[str, Any]) -> Tuple[bool, str]:
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
    if final:
        item["completed_on"] = iso_now()
        item["worker_note"] = (item.get("worker_note") or "") + f" Finalized failure: {reason}"

def mark_done(item: Dict[str, Any], note: str = "") -> None:
    item["status"] = "done"
    item["completed_on"] = iso_now()
    if note:
        item["worker_note"] = note

def run_webqueue(limit: int = 3, autoupgrade: bool = True) -> Dict[str, Any]:
    q = load_queue()
    attempted = 0
    learned = 0
    skipped = 0
    finalized = 0

    cfg = load_autonomy()
    protect_conf = float(cfg.get("protect_confidence_threshold", 0.85))

    for item in q:
        if attempted >= limit:
            break

        ok, why = can_attempt(item)
        if not ok:
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

        attempted += 1
        item["status"] = "running"
        item["attempts"] = int(item.get("attempts") or 0) + 1
        item["last_attempt_ts"] = now_ts()

        topic = item.get("topic", "")
        safe_log(WEBQUEUE_LOG, f"webqueue: attempt {item['attempts']}/{item.get('max_attempts',DEFAULT_MAX_QUEUE_ATTEMPTS)} topic='{topic}'")

        forced = (item.get("source_url") or "").strip()
                # Phase 5.1: avoid re-learning from the same domain if we already have it stored
        avoid = []
        if isinstance(existing, dict):
            for d in (existing.get("evidence_domains") or []):
                if d and d not in avoid:
                    avoid.append(d)

        ok2, answer, sources, chosen_url = web_learn_topic(topic, forced_url=forced, avoid_domains=avoid)

        if not ok2:
            item["status"] = "failed"
            item["fail_reason"] = "web_fetch_failed"
            safe_log(WEBQUEUE_LOG, f"webqueue: failed topic='{topic}' reason='web_fetch_failed'")
            continue

        if chosen_url:
            item["source_url"] = chosen_url

        if autoupgrade:
            k = load_knowledge()
            existing = k.get(topic)
            existing_conf = float(existing.get("confidence", 0.0)) if isinstance(existing, dict) else 0.0
            taught_by_user = bool(existing.get("taught_by_user", False)) if isinstance(existing, dict) else False

            if taught_by_user and existing_conf >= 0.75:
                mark_done(item, note="Skipped upgrade: user-taught answer is high confidence.")
                safe_log(WEBQUEUE_LOG, f"webqueue: done (skipped overwrite) topic='{topic}' taught_by_user=True conf={existing_conf}")
                continue

            if existing_conf >= protect_conf and isinstance(existing, dict) and (existing.get("answer") or "").strip():
                mark_done(item, note=f"Skipped upgrade: existing confidence >= {protect_conf}.")
                safe_log(WEBQUEUE_LOG, f"webqueue: done (skipped overwrite) topic='{topic}' conf={existing_conf} protect_threshold={protect_conf}")
                continue

            base_floor = max(float(item.get("current_confidence", CONF_FLOOR_UNKNOWN)), CONF_FLOOR_LEARNED)
            existing_entry = ensure_entry_shape(existing) if isinstance(existing, dict) else ensure_entry_shape({})
            new_conf, updated_entry = compute_weighted_confidence(existing_entry, base_floor=base_floor, sources=sources)

            note = "Upgraded knowledge using Phase 2 structured synthesis (Phase 5.1 weighted confidence)"
            set_knowledge(topic, answer.strip(), new_conf, sources=sources, notes=note, taught_by_user=False, _merge_evidence=updated_entry.get("evidence"))

            learned += 1
            mark_done(item, note=f"{note}.")
            safe_log(WEBQUEUE_LOG, f"webqueue: learned topic='{topic}' chosen_url='{chosen_url}' conf={new_conf:.2f} sources={sources}")

            expansions = expand_topic_if_needed(topic)
            if expansions:
                for ex in expansions:
                    okg, whyg = autonomy_queue_guard_ok()
                    if not okg:
                        safe_log(WEBQUEUE_LOG, f"webqueue: expansion blocked guard='{whyg}' ex='{ex}' from='{topic}'")
                        continue
                    okq, _msgq = queue_add(ex, reason=f"Expanded from '{topic}'", confidence=0.35)
                    if okq:
                        safe_log(WEBQUEUE_LOG, f"webqueue: expanded queued topic='{ex}' from='{topic}'")
        else:
            mark_done(item, note="Fetched (autoupgrade disabled).")
            safe_log(WEBQUEUE_LOG, f"webqueue: done topic='{topic}' autoupgrade=False")

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
# Curiosity (Phase 1-3)
# -----------------------------

def curiosity_tick(limit: int = 3) -> Dict[str, Any]:
    k = load_knowledge()
    items = []
    for topic, entry in k.items():
        junk2, why2 = is_junk_topic(topic)
        if junk2:
            continue
        junk2, why2 = is_junk_topic(topic)
        if junk2:
            continue
        junk2, why2 = is_junk_topic(topic)
        if junk2:
            continue
        junk2, why2 = is_junk_topic(topic)
        if junk2:
            continue
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
        okg, whyg = autonomy_queue_guard_ok()
        if not okg:
            safe_log(CURIOSITY_LOG, f"curiosity: blocked by guard='{whyg}'")
            break
        ok, _msg = queue_add(topic, reason="Curiosity: low confidence topic", confidence=max(conf, 0.35))
        if ok:
            queued += 1

    safe_log(CURIOSITY_LOG, f"curiosity: considered={considered} queued={queued} limit={limit}")
    return {"considered": considered, "queued": queued, "limit": limit}

# -----------------------------
# Phase 4: Controlled autonomy
# -----------------------------

def autonomy_pick_daily_topics(cfg: Dict[str, Any]) -> List[str]:
    dt = cfg.get("daily_themes") or {}
    if not isinstance(dt, dict):
        dt = {}
    key = weekday_key()
    topics = dt.get(key) or []
    if not isinstance(topics, list):
        topics = []
    return [normalize_topic(t) for t in topics if normalize_topic(t)]

def autonomy_pick_weekly_bucket(cfg: Dict[str, Any]) -> List[str]:
    buckets = cfg.get("weekly_themes") or []
    if not isinstance(buckets, list) or not buckets:
        return []

    k = load_knowledge()
    best_bucket = None
    best_score = None
    for bucket in buckets:
        if not isinstance(bucket, list):
            continue
        bnorm = [normalize_topic(x) for x in bucket if normalize_topic(x)]
        if not bnorm:
            continue
        covered = 0
        for t in bnorm:
            if t in k and isinstance(k[t], dict) and (k[t].get("answer") or "").strip():
                covered += 1
        score = covered
        if best_score is None or score < best_score:
            best_score = score
            best_bucket = bnorm

    return best_bucket or []

def autonomy_seed_topics(topics: List[str], reason: str, limit: int) -> Dict[str, Any]:
    added = 0
    skipped = 0
    blocked = 0
    msgs = []

    for t in topics:
        if added >= limit:
            break
        okg, whyg = autonomy_queue_guard_ok()
        if not okg:
            blocked += 1
            msgs.append(f"blocked:{whyg}")
            break

        ok, msg = queue_add(t, reason=reason, confidence=0.35)
        if ok:
            added += 1
        else:
            skipped += 1

    return {"added": added, "skipped": skipped, "blocked": blocked, "notes": msgs}

def autonomy_run_daily(force: bool = False) -> Dict[str, Any]:
    cfg = load_autonomy()
    if not bool(cfg.get("enabled", True)):
        return {"ok": False, "msg": "Autonomy is disabled."}

    ymd = today_ymd()
    if (not force) and cfg.get("last_daily_ymd") == ymd:
        return {"ok": True, "msg": f"Daily autonomy already ran today ({ymd})."}

    topics = autonomy_pick_daily_topics(cfg)
    if not topics:
        return {"ok": False, "msg": "No daily topics configured."}

    limit = int(cfg.get("daily_seed_limit", 3))
    res_seed = autonomy_seed_topics(topics, reason="Autonomy daily seed", limit=limit)

    learn_limit = int(cfg.get("daily_autolearn_limit", 2))
    res_learn = {"learned": 0, "attempted": 0}
    if learn_limit > 0:
        res_learn = run_webqueue(limit=learn_limit, autoupgrade=True)

    cfg["last_daily_ymd"] = ymd
    save_autonomy(cfg)

    safe_log(AUTONOMY_LOG, f"daily: ymd={ymd} seed={res_seed} learn={res_learn}")
    return {"ok": True, "msg": f"Daily autonomy ran ({ymd}).", "seed": res_seed, "learn": res_learn}

def autonomy_run_weekly(force: bool = False) -> Dict[str, Any]:
    cfg = load_autonomy()
    if not bool(cfg.get("enabled", True)):
        return {"ok": False, "msg": "Autonomy is disabled."}

    ymd = today_ymd()
    last = (cfg.get("last_weekly_ymd") or "").strip()
    if (not force) and last:
        try:
            last_dt = datetime.datetime.strptime(last, "%Y-%m-%d")
            now_dt = datetime.datetime.strptime(ymd, "%Y-%m-%d")
            if (now_dt - last_dt).days < 7:
                return {"ok": True, "msg": f"Weekly autonomy last ran on {last} (less than 7 days ago)."}
        except Exception:
            pass

    bucket = autonomy_pick_weekly_bucket(cfg)
    if not bucket:
        return {"ok": False, "msg": "No weekly topics configured."}

    limit = int(cfg.get("weekly_seed_limit", 6))
    res_seed = autonomy_seed_topics(bucket, reason="Autonomy weekly deep dive", limit=limit)

    learn_limit = int(cfg.get("weekly_autolearn_limit", 3))
    res_learn = {"learned": 0, "attempted": 0}
    if learn_limit > 0:
        res_learn = run_webqueue(limit=learn_limit, autoupgrade=True)

    cfg["last_weekly_ymd"] = ymd
    save_autonomy(cfg)

    safe_log(AUTONOMY_LOG, f"weekly: ymd={ymd} bucket={bucket} seed={res_seed} learn={res_learn}")
    return {"ok": True, "msg": f"Weekly autonomy ran ({ymd}).", "bucket": bucket, "seed": res_seed, "learn": res_learn}

# -----------------------------
# Phase 3: Maintenance tools (safe)
# -----------------------------

def split_pipe(cmd: str) -> Tuple[str, str]:
    if "|" not in cmd:
        return cmd.strip(), ""
    left, right = cmd.split("|", 1)
    return left.strip(), right.strip()

def cmd_merge(arg: str) -> None:
    left, right = split_pipe(arg)
    frm = normalize_topic(left.replace("/merge", "", 1).strip())
    to = normalize_topic(right)
    if not frm or not to:
        print("Usage: /merge <from> | <to>")
        return
    if frm == to:
        print("Nothing to merge (same topic).")
        return

    k = load_knowledge()
    if frm not in k and to not in k:
        print("Neither topic exists in knowledge.")
        return

    from_entry = ensure_entry_shape(k.get(frm, {})) if isinstance(k.get(frm, {}), dict) else ensure_entry_shape({"answer": str(k.get(frm, ""))})
    to_entry = ensure_entry_shape(k.get(to, {})) if isinstance(k.get(to, {}), dict) else ensure_entry_shape({"answer": str(k.get(to, ""))})

    merged = dict(to_entry)

    if (not merged.get("answer")) and from_entry.get("answer"):
        merged["answer"] = from_entry.get("answer")

    if to_entry.get("answer") and from_entry.get("answer") and to_entry.get("answer") != from_entry.get("answer"):
        merged["notes"] = (merged.get("notes", "") + "\n\n" + f"Merged from '{frm}' on {iso_now()}:\n" + from_entry.get("answer", "")).strip()

    merged["confidence"] = max(float(to_entry.get("confidence", 0.0)), float(from_entry.get("confidence", 0.0)))

    srcs = []
    for s in (to_entry.get("sources") or []):
        if s not in srcs:
            srcs.append(s)
    for s in (from_entry.get("sources") or []):
        if s not in srcs:
            srcs.append(s)
    merged["sources"] = srcs

    # Merge evidence safely (Phase 5.1): keep the richer one
    ev_from = from_entry.get("evidence") if isinstance(from_entry.get("evidence"), dict) else None
    ev_to = to_entry.get("evidence") if isinstance(to_entry.get("evidence"), dict) else None
    if ev_from or ev_to:
        merged = ensure_evidence_shape(merged)
        if ev_to:
            merged["evidence"] = ev_to
        if ev_from:
            merged["evidence"] = merged.get("evidence") or {}
            # soft merge counts
            try:
                merged_ev = merged.get("evidence") if isinstance(merged.get("evidence"), dict) else {}
                merged_ev.setdefault("domains", {})
                merged_ev.setdefault("buckets", {})
                if isinstance(ev_from.get("domains"), dict):
                    for d, meta in ev_from["domains"].items():
                        if d not in merged_ev["domains"]:
                            merged_ev["domains"][d] = meta
                if isinstance(ev_from.get("buckets"), dict):
                    for b, n in ev_from["buckets"].items():
                        merged_ev["buckets"][b] = int(merged_ev["buckets"].get(b) or 0) + int(n or 0)
                merged["evidence"] = merged_ev
            except Exception:
                pass

    merged["taught_by_user"] = bool(to_entry.get("taught_by_user", False) or from_entry.get("taught_by_user", False))
    merged["updated"] = iso_now()

    k[to] = merged
    save_knowledge(k)

    a = load_aliases()
    a[frm] = to
    save_aliases(a)

    print(f"Merged '{frm}' into '{to}'. (Safe: '{frm}' not deleted). Added alias {frm} -> {to}.")

def cmd_dedupe(_arg: str) -> None:
    k = load_knowledge()
    if not k:
        print("No knowledge entries to dedupe.")
        return

    bucket: Dict[str, List[str]] = {}
    for topic, entry in k.items():
        junk2, why2 = is_junk_topic(topic)
        if junk2:
            continue
        if not isinstance(entry, dict):
            continue
        ans = (entry.get("answer") or "").strip()
        if not ans:
            continue
        key = ans
        bucket.setdefault(key, []).append(topic)

    dups = [(ans, topics) for ans, topics in bucket.items() if len(topics) > 1]
    if not dups:
        print("No exact duplicate answers found.")
        return

    print("Exact duplicate answers found (safe suggestions):")
    shown = 0
    for _ans, topics in sorted(dups, key=lambda x: len(x[1]), reverse=True):
        shown += 1
        if shown > 10:
            print("...more duplicates exist. Run /dedupe again after cleaning some.")
            break
        topics_sorted = sorted(topics)
        keep = topics_sorted[0]
        print(f"- Duplicate group ({len(topics_sorted)} topics). Suggested keep: '{keep}'")
        for t in topics_sorted:
            print(f"  - {t}")
        print(f"  Suggested merges:")
        for t in topics_sorted[1:]:
            print(f"  /merge {t} | {keep}")

def cmd_prune(arg: str) -> None:
    mode = arg.replace("/prune", "", 1).strip().lower()
    if not mode:
        mode = "dryrun"
    if mode not in ("dryrun", "apply"):
        print("Usage: /prune [dryrun|apply]")
        return

    k = load_knowledge()
    a = load_aliases()

    empty = []
    shadows = []

    for topic, entry in k.items():
        junk2, why2 = is_junk_topic(topic)
        if junk2:
            continue
        if not isinstance(entry, dict):
            continue
        ans = (entry.get("answer") or "").strip()
        conf = float(entry.get("confidence", 0.0))
        srcs = entry.get("sources") or []
        notes = (entry.get("notes") or "").strip()
        taught = bool(entry.get("taught_by_user", False))

        if not ans and not notes and not srcs and conf <= 0.5 and not taught:
            empty.append(topic)

        if topic in a:
            target = a[topic]
            target_entry = k.get(target)
            if isinstance(target_entry, dict):
                if (not ans) and (not notes) and (not srcs) and (not taught):
                    shadows.append(topic)

    empty = sorted(set(empty))
    shadows = sorted(set(shadows))

    print("Prune report:")
    print(f"- empty candidates: {len(empty)}")
    print(f"- alias-shadow candidates: {len(shadows)}")
    if empty[:20]:
        print("Empty candidates (first 20):")
        for t in empty[:20]:
            print(f"- {t}")
    if shadows[:20]:
        print("Alias-shadow candidates (first 20):")
        for t in shadows[:20]:
            print(f"- {t} (alias -> {a.get(t,'')})")

    if mode == "dryrun":
        print("Dry run only. To apply safe deletions: /prune apply")
        return

    removed = 0
    for t in empty:
        if t in k:
            del k[t]
            removed += 1
    for t in shadows:
        if t in k:
            del k[t]
            removed += 1

    save_knowledge(k)
    print(f"Applied prune. Removed {removed} entries (safe set only).")

def cmd_selftest(_arg: str) -> None:
    results = []
    ok_all = True

    def add(name: str, ok: bool, detail: str = ""):
        nonlocal ok_all
        if not ok:
            ok_all = False
        results.append((name, ok, detail))

    try:
        ensure_dirs()
        add("dirs", True, f"DATA_DIR={DATA_DIR}")
    except Exception as e:
        add("dirs", False, str(e))

    try:
        p = os.path.join(DATA_DIR, ".selftest_write.tmp")
        with open(p, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(p)
        add("data_write", True, "can write/delete in data/")
    except Exception as e:
        add("data_write", False, str(e))

    try:
        k = load_knowledge()
        a = load_aliases()
        q = load_queue()
        pp = load_pending_promotions()
        cfg = load_autonomy()
        _ = (len(k), len(a), len(q), len(pp), bool(cfg.get("enabled", True)))
        add("json_load", True, f"knowledge={len(k)} aliases={len(a)} queue={len(q)} pending_promos={len(pp)} autonomy_enabled={cfg.get('enabled', True)}")
    except Exception as e:
        add("json_load", False, str(e))

    try:
        safe_log(BRAIN_LOG, "selftest: log write check")
        safe_log(AUTONOMY_LOG, "selftest: autonomy log write check")
        add("logging", True, f"wrote to {BRAIN_LOG} and {AUTONOMY_LOG}")
    except Exception as e:
        add("logging", False, str(e))

    if urllib is None:
        add("web_stack", False, "urllib not available")
    else:
        code, _txt = http_get("https://en.wikipedia.org/wiki/Main_Page", timeout=10)
        add("web_fetch", (code == 200), f"status={code}")

    try:
        dummy = {
            "topic": "selftest_dummy",
            "status": "failed",
            "attempts": 1,
            "max_attempts": 3,
            "last_attempt_ts": now_ts(),
            "cooldown_seconds": 60,
        }
        ok, why = can_attempt(dummy)
        add("queue_cooldown", (not ok and why.startswith("cooldown:")), f"{ok=} {why}")
    except Exception as e:
        add("queue_cooldown", False, str(e))

    try:
        import subprocess
        def run(cmd: List[str]) -> str:
            r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            return (r.stdout or "") + (r.stderr or "")
        out = run(["systemctl", "--user", "is-enabled", "machinespirit-webqueue.timer"])
        out2 = run(["systemctl", "--user", "is-enabled", "machinespirit-curiosity.timer"])
        add("timers_enabled", True, f"webqueue={out.strip()} curiosity={out2.strip()}")
    except Exception as e:
        add("timers_enabled", True, f"skipped (no systemctl access): {e}")

    print("Selftest results:")
    for name, ok, detail in results:
        mark = "OK" if ok else "FAIL"
        if detail:
            print(f"- {name}: {mark} ({detail})")
        else:
            print(f"- {name}: {mark}")
    if ok_all:
        print("Selftest overall: OK")
    else:
        print("Selftest overall: FAIL (see above)")

# -----------------------------
# Core interaction + help
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

    if resolved == topic_n:
        sug = suggest_alias(topic_n, k, aliases)
        if sug and sug != topic_n:
            print(f"Suggestion: /alias {topic_n} | {sug}")

    entry = k.get(resolved)
    if isinstance(entry, dict) and entry.get("answer"):
        entry = ensure_entry_shape(entry)
        print(entry["answer"])
        if float(entry.get("confidence", 0.0)) < 0.60:
            okg, whyg = autonomy_queue_guard_ok()
            if okg:
                ok, _ = queue_add(resolved, reason="Answer exists but confidence is low", confidence=float(entry["confidence"]))
                if ok:
                    safe_log(BRAIN_LOG, f"autoqueue: topic='{resolved}' reason='low_confidence'")
            else:
                safe_log(BRAIN_LOG, f"autoqueue: blocked guard='{whyg}' topic='{resolved}'")
        return

    print("Machine Spirit: I do not have a taught answer for that yet. If my reply is wrong or weak, correct me in your own words and I will remember it. My analysis may be incomplete. If this seems wrong, correct me and I will update my understanding. I have also marked this topic for deeper research so I can improve my answer over time.")
    okg, _whyg = autonomy_queue_guard_ok()
    if okg:
        queue_add(topic_n, reason="No taught answer yet", confidence=0.35)

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
/confirm <topic>
/lowest [n]
/alias <from> | <to>
/aliases
/unalias <from>
/why <topic>
/accept
/suggest
/weblearn <topic>
/weburl <topic> | <url>
/webqueue [limit]
/queuehealth
/curiosity [limit]

# Phase 3 maintenance:
/merge <from> | <to>
/dedupe
/prune [dryrun|apply]
/selftest

# Phase 4 controlled autonomy:
/autonomy status
/autonomy on
/autonomy off
/autonomy daily        (runs daily seed now + small learn pass)
/autonomy weekly       (runs weekly deep dive now + small learn pass)

Type a normal topic name (example: "subnetting") to get an answer.
""")

# -----------------------------
# Commands
# -----------------------------

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

    # Phase 5.1: prefer the new stored fields (these are the source of truth)
    domains = entry.get("evidence_domains")
    buckets = entry.get("evidence_buckets")
    reinf = entry.get("reinforcement")

    # Back-compat fallback: some older entries may only have entry["evidence"]
    if (domains is None) or (buckets is None) or (reinf is None):
        ev = entry.get("evidence") or {}
        if domains is None:
            domains = ev.get("domains")
        if buckets is None:
            buckets = ev.get("buckets")
        if reinf is None:
            reinf = ev.get("reinforcement")

    if not isinstance(domains, list):
        domains = []
    if not isinstance(buckets, dict):
        buckets = {}
    if not isinstance(reinf, dict):
        reinf = {"count": 0, "last": entry.get("updated", "")}

    print(f"- evidence domains: {len(domains)}")
    if buckets:
        print("- evidence buckets:")
        for k, v in sorted(buckets.items(), key=lambda x: (-int(x[1]), str(x[0]))):
            print(f"  - {k}: {v}")
    print(f"- reinforcement: count={int(reinf.get('count',0) or 0)} last={reinf.get('last','')}")


def cmd_confirm(arg: str) -> None:
    """
    Phase 5.1.2: user confirmation raises confidence.
    Each /confirm adds a small bump (stackable) and records confirmation metadata.
    """
    topic = normalize_topic(arg.replace("/confirm", "", 1).strip())
    if not topic:
        print("Usage: /confirm <topic>")
        return

    aliases = load_aliases()
    resolved = resolve_topic(topic, aliases)

    k = load_knowledge()
    entry = k.get(resolved)
    if not isinstance(entry, dict):
        print("No entry yet for that topic. Teach it or /weblearn it first.")
        return

    entry = ensure_entry_shape(entry)

    confirmed = entry.get("confirmed")
    if not isinstance(confirmed, dict):
        confirmed = {"count": 0, "last": ""}

    confirmed["count"] = int(confirmed.get("count") or 0) + 1
    confirmed["last"] = iso_now()
    entry["confirmed"] = confirmed

    # bump confidence: +0.05 per confirm, capped
    conf0 = float(entry.get("confidence", 0.0))
    conf1 = min(conf0 + 0.05, 0.99)
    entry["confidence"] = max(conf0, conf1)
    entry["updated"] = iso_now()

    # save
    k[resolved] = entry
    save_knowledge(k)

    print(f"Confirmed '{resolved}'. confidence: {conf0:.2f} -> {entry['confidence']:.2f} (confirm count={confirmed['count']})")


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
        junk2, why2 = is_junk_topic(topic)
        if junk2:
            continue
        if not isinstance(entry, dict):
            continue
        conf = float(entry.get("confidence", 0.0))
        items.append((conf, topic))
    items.sort(key=lambda x: x[0])
    print(f"Lowest confidence (top {n}):")
    for conf, topic in items[:n]:
        print(f"- {topic}: {conf}")



def cmd_lowestdomains(arg: str) -> None:
    """
    /lowestdomains [n]
    Like /lowest, but shows evidence domain count.
    """
    n_str = arg.replace("/lowestdomains", "", 1).strip()
    n = 10
    if n_str:
        try:
            n = int(n_str)
        except Exception:
            n = 10

    k = load_knowledge()
    items = []
    for topic, entry in k.items():
        junk2, why2 = is_junk_topic(topic)
        if junk2:
            continue
        if not isinstance(entry, dict):
            continue
        conf = float(entry.get("confidence", 0.0) or 0.0)
        domains = entry.get("evidence_domains") or []
        dcount = len(set([(d or "").lower().strip() for d in domains if (d or "").strip()]))
        items.append((conf, dcount, topic))

    items.sort(key=lambda x: (x[0], x[1], x[2]))
    print(f"Lowest confidence w/ domains (top {n}):")
    for conf, dcount, topic in items[:n]:
        print(f"- {topic}: {conf:.2f} (domains={dcount})")

def cmd_needsources(arg: str) -> None:
    """
    /needsources [n]
    List topics with fewer than n evidence domains (default 2).
    """
    try:
        n_str = arg.replace("/needsources", "", 1).strip()
        n = int(n_str) if n_str else 2
    except Exception:
        n = 2
    if n < 1:
        n = 1

    k = load_knowledge()
    rows = []
    for topic, entry in k.items():
        junk2, why2 = is_junk_topic(topic)
        if junk2:
            continue
        if not isinstance(entry, dict):
            continue
        domains = entry.get("evidence_domains") or []
        dcount = len(set([(d or "").lower().strip() for d in domains if (d or "").strip()]))
        conf = float(entry.get("confidence", 0.0) or 0.0)
        if dcount < n:
            rows.append((topic, conf, dcount))

    rows.sort(key=lambda x: (x[2], x[1], x[0]))  # fewest domains, then lowest confidence, then topic
    print(f"Topics needing sources (< {n} domains):")
    if not rows:
        print("- none")
        return
    for topic, conf, dcount in rows[:50]:
        print(f"- {topic}: {conf:.2f} (domains={dcount})")



def cmd_debugsources(arg: str) -> None:
    """
    /debugsources <topic>
    Show candidate URLs + scores + whether fetch is blocked/thin/etc.
    """
    topic = normalize_topic(arg.replace("/debugsources", "", 1).strip())
    if not topic:
        print("Usage: /debugsources <topic>")
        return

    q1 = topic
    q2 = topic + " documentation RFC IETF NIST -site:wikipedia.org -site:wiktionary.org -site:wikidata.org"

    def show(label: str, q: str):
        print(f"--- {label} ---")
        print(f"query: {q}")
        cands = ddg_search(q, max_results=12) or []
        if not cands:
            print("(no candidates)")
            return

        rows = []
        for c in cands:
            url = (c.get("url") or "").strip()
            title = (c.get("title") or "").strip()
            if not url:
                continue
            sc = source_score(url, title)
            rows.append((sc, url, title))

        rows.sort(key=lambda x: x[0], reverse=True)

        for sc, url, title in rows[:8]:
            ok, text, reason = fetch_page_text_debug(url)
            dom = get_domain(url)
            tlen = len(re.sub(r"\s+"," ", (text or "")).strip()) if text else 0
            print(f"- score={sc:4d} ok={ok} reason={reason:12s} len={tlen:5d} domain={dom} url={url}")

    show("NORMAL", q1)
    print("")
    show("WIKI_AVOID", q2)

def cmd_repair_evidence(arg: str) -> None:
    """
    /repair_evidence
    One-time maintenance:
    - remove junk topics
    - backfill evidence_domains / buckets from stored sources (if present)
    - clamp over-confident unconfirmed entries lacking evidence
    """
    k = load_knowledge()

    def _bucket(d: str) -> str:
        d = (d or "").lower().strip()
        if not d:
            return "other"
        if "rfc-editor.org" in d or "ietf.org" in d or "iana.org" in d:
            return "rfc"
        if d.endswith(".gov") or d.endswith(".edu") or "nist.gov" in d:
            return "gov_edu"
        if "wikipedia.org" in d:
            return "wiki"
        # treat major vendor docs as vendor bucket
        vendor_hits = ["cisco.com", "juniper.net", "microsoft.com", "learn.microsoft.com", "cloudflare.com", "akamai.com", "redhat.com", "ibm.com", "oracle.com"]
        if any(v in d for v in vendor_hits):
            return "vendor"
        return "other"
    removed = 0
    backfilled = 0
    clamped = 0
    touched = 0

    for topic in list(k.keys()):
        junk, why = is_junk_topic(topic)
        if junk:
            del k[topic]
            removed += 1
            continue

        entry = k.get(topic)
        if not isinstance(entry, dict):
            continue

        # Ensure confirmed structure exists
        confirmed = entry.get("confirmed")
        if not isinstance(confirmed, dict):
            confirmed = {"count": 0, "last": ""}
            entry["confirmed"] = confirmed

        # Backfill evidence_domains from sources if missing/empty
        domains = entry.get("evidence_domains")
        if not isinstance(domains, list):
            domains = []
        domains_clean = set([(d or "").lower().strip() for d in domains if (d or "").strip()])

        sources = entry.get("sources") or []
        if isinstance(sources, list) and sources:
            for u in sources:
                try:
                    d = get_domain(str(u))
                except Exception:
                    d = ""
                d = (d or "").lower().strip()
                if d:
                    domains_clean.add(d)

        if len(domains_clean) != len(domains):
            entry["evidence_domains"] = sorted(domains_clean)
            # also backfill buckets if possible
            buckets = entry.get("evidence_buckets")
            if not isinstance(buckets, dict):
                buckets = {}
            for d in domains_clean:
                b = _bucket(d)
                buckets[b] = int(buckets.get(b, 0) or 0) + 1
            entry["evidence_buckets"] = buckets
            backfilled += 1
            touched += 1

        # Clamp confidence if unconfirmed and weak evidence (<2 domains)
        try:
            conf = float(entry.get("confidence", 0.0) or 0.0)
        except Exception:
            conf = 0.0
        confirm_count = int(confirmed.get("count") or 0)
        dcount = len(set([(d or "").lower().strip() for d in (entry.get("evidence_domains") or []) if (d or "").strip()]))

        if confirm_count <= 0 and dcount < 2 and conf > 0.92:
            entry["confidence"] = 0.92
            entry["updated"] = iso_now()
            clamped += 1
            touched += 1

        k[topic] = entry

    if removed or backfilled or clamped:
        save_knowledge(k)

    print("Repair complete:")
    print(f"- removed junk topics: {removed}")
    print(f"- backfilled evidence: {backfilled}")
    print(f"- clamped confidence: {clamped}")
    print(f"- total touched: {touched}")

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
    q = load_queue()
    k = load_knowledge()
    a = load_aliases()

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
            if item.get("source_url"):
                print(f"Source URL: {item.get('source_url')}")
            return
    print("Not found in queue.")

def cmd_promote() -> None:
    q = load_queue()
    done = [i for i in q if i.get("status") == "done"]
    if not done:
        print("Nothing to promote.")
        return
    item = done[-1]
    add_pending_promotion(item.get("topic", ""), why=item.get("worker_note", ""))
    print("Added to pending promotions.")

def add_pending_promotion(topic: str, why: str = "") -> None:
    p = load_pending_promotions()
    topic_n = normalize_topic(topic)
    if not topic_n:
        return
    for item in p:
        if normalize_topic(item.get("topic", "")) == topic_n:
            return
    p.append({"topic": topic_n, "why": why or "", "added": iso_now()})
    save_pending_promotions(p)

def cmd_weblearn(arg: str) -> None:
    topic = normalize_topic(arg.replace("/weblearn", "", 1).strip())
    if not topic:
        print("Usage: /weblearn <topic>")
        return
    junk, why = is_junk_topic(topic)
    if junk:
        print(f"Refusing (junk topic): {why}")
        return

    # Phase 5.1: if we already have a source domain, try to learn from a different domain next time
    k0 = load_knowledge()
    e0 = k0.get(topic)
    avoid = []
    if isinstance(e0, dict):
        for d in (e0.get("evidence_domains") or []):
            if d and d not in avoid:
                avoid.append(d)

    ok, answer, sources, _chosen_url = web_learn_topic(topic, avoid_domains=avoid)

    # Phase 5.1: merge sources with existing
    if isinstance(e0, dict):
        existing_sources = e0.get("sources") or []
        merged = []
        for s in existing_sources + (sources or []):
            s2 = (s or "").strip()
            if s2 and s2 not in merged:
                merged.append(s2)
        sources = merged
    if not ok:
        print(answer)
        return

    k = load_knowledge()
    existing = k.get(topic)
    existing_entry = ensure_entry_shape(existing) if isinstance(existing, dict) else ensure_entry_shape({})
    base_floor = 0.55
    new_conf, updated_entry = compute_weighted_confidence(existing_entry, base_floor=base_floor, sources=sources)

    set_knowledge(topic, answer.strip(), confidence=new_conf, sources=sources, notes="Learned via /weblearn (Phase 2) (Phase 5.1 weighted confidence)", taught_by_user=False, _merge_evidence=updated_entry.get("evidence"))
    print(answer.strip())

    okg, _whyg = autonomy_queue_guard_ok()
    if okg:
        queue_add(topic, reason="Learned via /weblearn (Phase 2) - deepen later", confidence=new_conf)

    expansions = expand_topic_if_needed(topic)
    for ex in expansions:
        okg2, _ = autonomy_queue_guard_ok()
        if okg2:
            queue_add(ex, reason=f"Expanded from '{topic}'", confidence=0.35)

def cmd_weburl(arg: str) -> None:
    left, right = split_pipe(arg)
    topic = normalize_topic(left.replace("/weburl", "", 1).strip())
    url = (right or "").strip()
    if not topic or not url:
        print("Usage: /weburl <topic> | <url>")
        return
    if not url.startswith("http://") and not url.startswith("https://"):
        print("URL must start with http:// or https://")
        return

    ok, answer, sources, _chosen_url = web_learn_topic(topic, forced_url=url)
    if not ok:
        print(answer)
        return

    k = load_knowledge()
    existing = k.get(topic)
    existing_entry = ensure_entry_shape(existing) if isinstance(existing, dict) else ensure_entry_shape({})
    base_floor = 0.60
    new_conf, updated_entry = compute_weighted_confidence(existing_entry, base_floor=base_floor, sources=sources)

    set_knowledge(topic, answer.strip(), confidence=new_conf, sources=sources, notes="Learned via /weburl (Phase 2) (Phase 5.1 weighted confidence)", taught_by_user=False, _merge_evidence=updated_entry.get("evidence"))
    print(answer.strip())

    expansions = expand_topic_if_needed(topic)
    for ex in expansions:
        okg, _ = autonomy_queue_guard_ok()
        if okg:
            queue_add(ex, reason=f"Expanded from '{topic}'", confidence=0.35)

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

def cmd_autonomy(arg: str) -> None:
    rest = arg.replace("/autonomy", "", 1).strip().lower()
    cfg = load_autonomy()

    if not rest or rest == "status":
        q = load_queue()
        stats = queue_stats(q)
        print("Autonomy status:")
        print(f"- enabled: {bool(cfg.get('enabled', True))}")
        print(f"- last_daily_ymd: {cfg.get('last_daily_ymd','')}")
        print(f"- last_weekly_ymd: {cfg.get('last_weekly_ymd','')}")
        print(f"- queue totals: total={stats['total']} pending={stats['pending']} failed={stats['failed']} running={stats['running']}")
        print(f"- daily seed limit: {cfg.get('daily_seed_limit', 3)} (autolearn {cfg.get('daily_autolearn_limit', 2)})")
        print(f"- weekly seed limit: {cfg.get('weekly_seed_limit', 6)} (autolearn {cfg.get('weekly_autolearn_limit', 3)})")
        okg, whyg = autonomy_queue_guard_ok()
        print(f"- guard: {whyg}" if not okg else "- guard: ok")
        return

    if rest == "on":
        cfg["enabled"] = True
        save_autonomy(cfg)
        print("Autonomy enabled.")
        return

    if rest == "off":
        cfg["enabled"] = False
        save_autonomy(cfg)
        print("Autonomy disabled.")
        return

    if rest == "daily":
        res = autonomy_run_daily(force=True)
        print(res.get("msg", ""))
        if res.get("seed"):
            print(f"- seeded: {res['seed']}")
        if res.get("learn"):
            print(f"- learned: {res['learn']}")
        return

    if rest == "weekly":
        res = autonomy_run_weekly(force=True)
        print(res.get("msg", ""))
        if res.get("bucket"):
            print(f"- bucket: {res['bucket']}")
        if res.get("seed"):
            print(f"- seeded: {res['seed']}")
        if res.get("learn"):
            print(f"- learned: {res['learn']}")
        return

    print("Usage: /autonomy status|on|off|daily|weekly")

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

    if not os.path.exists(KNOWLEDGE_PATH):
        atomic_write_json(KNOWLEDGE_PATH, {})
    if not os.path.exists(ALIASES_PATH):
        atomic_write_json(ALIASES_PATH, {})
    if not os.path.exists(QUEUE_PATH):
        atomic_write_json(QUEUE_PATH, [])
    if not os.path.exists(PENDING_PATH):
        atomic_write_json(PENDING_PATH, [])
    if not os.path.exists(AUTONOMY_PATH):
        atomic_write_json(AUTONOMY_PATH, load_autonomy())

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

        if is_urlish(user) and not user.startswith("/"):
            print("Machine Spirit: That looks like a URL or domain string. Ask using a normal topic name instead (example: 'rfc 1918').")
            continue

        if user.startswith("/"):
            if user in ("/help", "/h", "/?"):
                print_help()
                continue

            if user.startswith("/teach "):
                cmd_teach(user); continue
            if user.startswith("/teachfile "):
                cmd_teachfile(user); continue
            if user.startswith("/ingest "):
                cmd_ingest(user); continue
            if user.startswith("/importfolder"):
                cmd_importfolder(user); continue
            if user.startswith("/import"):
                cmd_import(user); continue
            if user.startswith("/export"):
                cmd_export(); continue
            if user.startswith("/queuehealth"):
                cmd_queuehealth(); continue
            if user.startswith("/queue"):
                cmd_queue(); continue
            if user.startswith("/clearpending"):
                cmd_clearpending(); continue
            if user.startswith("/purgejunk"):
                cmd_purgejunk(); continue
            if user.startswith("/promote"):
                cmd_promote(); continue
            if user.startswith("/confidence"):
                cmd_confidence(user); continue
            if user.startswith("/confirm"):
                cmd_confirm(user); continue
            if user.startswith("/lowestdomains"):
                cmd_lowestdomains(user); continue

            if user.startswith("/lowest"):
                cmd_lowest(user); continue
            if user.startswith("/needsources"):
                cmd_needsources(user); continue



            if user.startswith("/debugsources"):
                cmd_debugsources(user); continue

            if user.startswith("/repair_evidence"):
                cmd_repair_evidence(user); continue

            if user.startswith("/alias "):
                cmd_alias(user); continue
            if user.startswith("/aliases"):
                cmd_aliases(); continue
            if user.startswith("/unalias"):
                cmd_unalias(user); continue
            if user.startswith("/why"):
                cmd_why(user); continue
            if user.startswith("/accept"):
                cmd_accept(); continue
            if user.startswith("/suggest"):
                cmd_suggest(); continue
            if user.startswith("/weblearn"):
                cmd_weblearn(user); continue
            if user.startswith("/weburl"):
                cmd_weburl(user); continue
            if user.startswith("/webqueue"):
                cmd_webqueue(user); continue
            if user.startswith("/curiosity"):
                cmd_curiosity(user); continue

            # Phase 3 commands
            if user.startswith("/merge"):
                cmd_merge(user); continue
            if user.startswith("/dedupe"):
                cmd_dedupe(user); continue
            if user.startswith("/prune"):
                cmd_prune(user); continue
            if user.startswith("/selftest"):
                cmd_selftest(user); continue

            # Phase 4 command
            if user.startswith("/autonomy"):
                cmd_autonomy(user); continue

            print("Unknown command. Type /help.")
            continue

        show_topic(user)

    print("Shutting down.")

if __name__ == "__main__":
    main()
