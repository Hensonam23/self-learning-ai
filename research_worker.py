from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from datetime import datetime, timezone


APP_ROOT = Path(__file__).resolve().parent

RESEARCH_QUEUE_PATH = APP_ROOT / "data" / "research_queue.json"
RESEARCH_NOTES_PATH = APP_ROOT / "data" / "research_notes.json"
BASE_KNOWLEDGE_PATH = APP_ROOT / "data" / "knowledge" / "base_knowledge.json"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_key_simple(text: str) -> str:
    key = (text or "").strip().lower()
    key = key.replace("—", " ").replace("–", " ")
    key = re.sub(r"[?!.]+$", "", key).strip()
    return key


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def ensure_files_exist() -> None:
    if not RESEARCH_QUEUE_PATH.exists():
        save_json(RESEARCH_QUEUE_PATH, {"queue": []})
    if not RESEARCH_NOTES_PATH.exists():
        save_json(RESEARCH_NOTES_PATH, {"drafts": {}})


def coerce_queue_structure(queue_data):
    if isinstance(queue_data, dict):
        q = queue_data.get("queue", [])
        return {"queue": q if isinstance(q, list) else []}
    if isinstance(queue_data, list):
        return {"queue": queue_data}
    return {"queue": []}


def coerce_notes_structure(notes_data):
    if isinstance(notes_data, dict):
        if "drafts" in notes_data and isinstance(notes_data["drafts"], dict):
            return {"drafts": notes_data["drafts"]}
        return {"drafts": notes_data}
    return {"drafts": {}}


def make_keywords(topic: str) -> list[str]:
    raw = topic.lower().replace("/", " ").replace("-", " ").replace("_", " ")
    raw = raw.replace("—", " ").replace("–", " ")
    parts = [p.strip() for p in raw.split() if p.strip()]
    stop = {
        "the", "a", "an", "and", "or", "to", "of", "in", "for", "on", "with",
        "is", "are", "between", "difference", "vs", "versus"
    }
    parts = [p for p in parts if p not in stop]
    out = []
    for p in parts:
        if p not in out:
            out.append(p)
    return out[:12]


# -------------------------
# Style learning (offline)
# -------------------------
def load_style_from_base_knowledge() -> dict:
    base = load_json(BASE_KNOWLEDGE_PATH, {"items": {}})
    items = base.get("items", {})
    if not isinstance(items, dict) or not items:
        return {"max_sentence_words": 18, "samples": []}

    keys = list(items.keys())[-4:]
    samples = []
    word_counts = []

    for k in keys:
        ans = str(items.get(k, {}).get("answer", "")).strip()
        if not ans:
            continue
        sample = ans.replace("\r", "").strip()[:240]
        samples.append(sample)

        sentences = re.split(r"[.!?]\s+", sample)
        for s in sentences:
            s = s.strip()
            if not s:
                continue
            wc = len(s.split())
            if wc > 0:
                word_counts.append(wc)

    if word_counts:
        avg = sum(word_counts) / len(word_counts)
        max_sentence_words = max(12, min(22, int(round(avg + 2))))
    else:
        max_sentence_words = 18

    return {"max_sentence_words": max_sentence_words, "samples": samples[:4]}


def remove_em_dashes(text: str) -> str:
    return text.replace("—", ". ").replace("–", ". ")


def normalize_spaces(text: str) -> str:
    text = text.replace("\t", " ")
    text = re.sub(r"[ ]{2,}", " ", text)
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return text.strip()


def shorten_sentences(text: str, max_words: int) -> str:
    out_lines = []
    for line in text.splitlines():
        raw = line.rstrip()
        if not raw:
            out_lines.append("")
            continue

        m = re.match(r"^(\s*\d+\)\s+)(.*)$", raw)
        prefix = ""
        body = raw
        if m:
            prefix = m.group(1)
            body = m.group(2)

        parts = re.split(r"(?<=[.!?])\s+|;\s+|:\s+", body)
        rebuilt = []
        for p in parts:
            p = p.strip()
            if not p:
                continue
            words = p.split()
            if len(words) <= max_words:
                rebuilt.append(p)
            else:
                chunk = []
                for w in words:
                    chunk.append(w)
                    if len(chunk) >= max_words:
                        rebuilt.append(" ".join(chunk).strip() + ".")
                        chunk = []
                if chunk:
                    rebuilt.append(" ".join(chunk).strip())

        out_lines.append(prefix + " ".join(rebuilt).strip())

    return "\n".join(out_lines).strip()


def apply_student_voice(text: str, style: dict) -> str:
    t = remove_em_dashes(text)
    t = normalize_spaces(t)
    t = shorten_sentences(t, style.get("max_sentence_words", 18))
    t = normalize_spaces(t)
    return t


# -------------------------
# Weak draft detection
# -------------------------
def is_weak_text(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return True
    weak_markers = [
        "write a clean",
        "main point",
        "(one sentence)",
        "( )",
        "basic draft",
        "i made a draft",
        "might be rough",
        "needs a clean",
    ]
    if any(m in t for m in weak_markers):
        return True
    if len(t) < 40:
        return True
    return False


def draft_is_weak(existing: dict) -> bool:
    if not isinstance(existing, dict):
        return True
    short = existing.get("short", "")
    detailed = existing.get("detailed", existing.get("answer", ""))
    return is_weak_text(short) or is_weak_text(detailed)


# -------------------------
# Compare draft detection
# -------------------------
def parse_compare_topic(topic: str) -> tuple[str, str] | None:
    t = topic.strip()
    tl = t.lower()

    m = re.search(r"difference\s+between\s+(.+?)\s+and\s+(.+)$", tl)
    if m:
        a = t[m.start(1) : m.end(1)].strip()
        b = t[m.start(2) : m.end(2)].strip()
        if a and b:
            return a, b

    if " vs " in tl:
        parts = re.split(r"\s+vs\s+", t, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            return parts[0].strip(), parts[1].strip()

    if " versus " in tl:
        parts = re.split(r"\s+versus\s+", t, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            return parts[0].strip(), parts[1].strip()

    return None


def draft_compare(topic: str, a: str, b: str) -> tuple[str, str]:
    al = a.lower().strip()
    bl = b.lower().strip()

    if (("tcp" in al and "udp" in bl) or ("udp" in al and "tcp" in bl)):
        short = (
            "TCP vs UDP: TCP is reliable and connection based. UDP is faster and lighter but not guaranteed.\n"
            "TCP is used for web and downloads. UDP is used for streaming and games a lot."
        )
        detailed = (
            "TCP vs UDP (draft)\n\n"
            "Simple definition:\n"
            "1) TCP is connection based and focuses on reliability.\n"
            "2) UDP is connectionless and focuses on speed and low overhead.\n\n"
            "Main differences:\n"
            "1) Reliability. TCP retries and orders data. UDP does not guarantee delivery or order.\n"
            "2) Setup. TCP does a handshake. UDP just sends.\n"
            "3) Use cases. TCP is good for web pages, email, file transfers. UDP is common for voice, video, gaming.\n"
        )
        return short, detailed

    short = (
        f"{a} vs {b}: A compare draft.\n"
        "The main difference is what each one is used for."
    )
    detailed = (
        f"{a} vs {b} (draft)\n\n"
        "Simple definition:\n"
        f"1) {a} is: (one sentence)\n"
        f"2) {b} is: (one sentence)\n\n"
        "Main differences:\n"
        f"1) Purpose. {a} is mainly for ( ). {b} is mainly for ( ).\n"
        f"2) Troubleshooting. {a} problems look like ( ). {b} problems look like ( ).\n"
    )
    return short, detailed


# -------------------------
# Real offline recipes
# -------------------------
def draft_arp(topic: str):
    short = (
        "ARP: It finds the MAC address for an IPv4 address on your local network.\n"
        "A device asks who has an IP, and the owner replies with its MAC."
    )
    detailed = (
        "ARP (draft)\n\n"
        "Simple definition:\n"
        "1) ARP maps an IPv4 address to a MAC address on a local network.\n\n"
        "How it works:\n"
        "1) Your PC broadcasts an ARP request asking who has an IP.\n"
        "2) The device replies with its MAC address.\n"
        "3) Your PC stores it in an ARP cache for a while.\n"
    )
    return short, detailed


def draft_icmp(topic: str):
    short = (
        "ICMP: A control protocol used for testing and error messages on IP networks.\n"
        "Ping and traceroute often use ICMP."
    )
    detailed = (
        "ICMP (draft)\n\n"
        "Simple definition:\n"
        "1) ICMP is used for network status and error messages.\n"
        "2) It helps devices and routers report problems like unreachable networks.\n\n"
        "Common uses:\n"
        "1) Ping uses ICMP echo request and echo reply.\n"
        "2) Traceroute often uses ICMP time exceeded to show the path.\n\n"
        "Key point:\n"
        "1) ICMP is for diagnostics and control, not for sending application data like files.\n"
    )
    return short, detailed


def draft_vlan(topic: str):
    short = (
        "VLAN: A virtual LAN. It splits one physical network into separate logical networks.\n"
        "It helps with security and organization."
    )
    detailed = (
        "VLAN (draft)\n\n"
        "Simple definition:\n"
        "1) A VLAN is a logical network separated at Layer 2.\n\n"
        "Key points:\n"
        "1) VLANs separate groups like staff vs guests.\n"
        "2) A trunk port carries multiple VLANs between switches.\n"
        "3) VLANs need routing to talk to each other (inter-VLAN routing).\n"
    )
    return short, detailed


def better_fallback(topic: str):
    t = topic.strip()
    short = (
        f"{t}: I do not have a built in recipe for this yet.\n"
        "I made a basic draft. If it is wrong, teach a better answer."
    )
    detailed = (
        f"{t} (basic draft)\n\n"
        "Simple definition:\n"
        "1) Write a clean 1 to 2 sentence definition here.\n\n"
        "Key points:\n"
        "1) Main point.\n"
        "2) Main point.\n"
        "3) Main point.\n"
    )
    return short, detailed


def generate_drafts(topic: str) -> tuple[str, str]:
    comp = parse_compare_topic(topic)
    if comp:
        a, b = comp
        return draft_compare(topic, a, b)

    t = normalize_key_simple(topic)

    if t == "arp" or "address resolution protocol" in t:
        return draft_arp(topic)
    if t == "icmp" or "internet control message protocol" in t:
        return draft_icmp(topic)
    if t == "vlan" or "virtual lan" in t:
        return draft_vlan(topic)

    return better_fallback(topic)


def parse_max_tasks(argv: list[str]) -> int:
    if len(argv) < 2:
        return 1
    try:
        n = int(argv[1])
        if n <= 0:
            return 1
        return min(n, 25)
    except Exception:
        return 1


def main() -> None:
    ensure_files_exist()
    style = load_style_from_base_knowledge()

    max_tasks = parse_max_tasks(sys.argv)

    raw_queue = load_json(RESEARCH_QUEUE_PATH, {"queue": []})
    queue_data = coerce_queue_structure(raw_queue)

    raw_notes = load_json(RESEARCH_NOTES_PATH, {"drafts": {}})
    notes_data = coerce_notes_structure(raw_notes)

    queue = queue_data["queue"]
    if not queue:
        print("No pending research tasks. Queue is clear.")
        return

    drafts = notes_data["drafts"]
    wrote = 0
    refreshed = 0
    skipped = 0

    while queue and wrote < max_tasks:
        task = queue.pop(0)

        if isinstance(task, dict):
            topic = (task.get("topic") or "").strip()
            key = (task.get("key") or "").strip()
        else:
            topic = str(task).strip()
            key = ""

        if not topic and key:
            topic = key
        if not key:
            key = normalize_key_simple(topic)
        if not topic:
            topic = "unknown topic"

        # If draft exists and is NOT weak, skip writing a new one.
        existing = drafts.get(key)
        if existing and not draft_is_weak(existing):
            skipped += 1
            continue

        if existing and draft_is_weak(existing):
            refreshed += 1

        ks = make_keywords(topic)
        short, detailed = generate_drafts(topic)

        short = apply_student_voice(short, style)
        detailed = apply_student_voice(detailed, style)

        drafts[key] = {
            "topic": topic,
            "key": key,
            "keywords": ks,
            "short": short,
            "detailed": detailed,
            "created_at": utc_now_iso(),
            "source": "offline_recipe_style_v5_refresh_weak",
        }

        wrote += 1
        print(f"Wrote draft: {topic}")

    queue_data["queue"] = queue
    notes_data["drafts"] = drafts
    save_json(RESEARCH_QUEUE_PATH, queue_data)
    save_json(RESEARCH_NOTES_PATH, notes_data)

    print(f"Done. Wrote {wrote} draft(s). Refreshed {refreshed}. Skipped {skipped}. Remaining in queue: {len(queue)}")


if __name__ == "__main__":
    main()
