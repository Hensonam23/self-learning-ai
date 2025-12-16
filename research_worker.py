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
    stop = {"the", "a", "an", "and", "or", "to", "of", "in", "for", "on", "with", "is", "are", "between"}
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
# Compare draft detection
# -------------------------
def parse_compare_topic(topic: str) -> tuple[str, str] | None:
    t = topic.strip()
    tl = t.lower()

    # "difference between X and Y"
    m = re.search(r"difference\s+between\s+(.+?)\s+and\s+(.+)$", tl)
    if m:
        a = t[m.start(1) : m.end(1)].strip()
        b = t[m.start(2) : m.end(2)].strip()
        if a and b:
            return a, b

    # "X vs Y" or "X versus Y"
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
    short = (
        f"{a} vs {b}: A quick compare draft.\n"
        "The main difference is what each one is used for."
    )
    detailed = (
        f"{a} vs {b} (draft)\n\n"
        "Simple definition:\n"
        f"1) {a} is: (one sentence)\n"
        f"2) {b} is: (one sentence)\n\n"
        "Main differences:\n"
        f"1) Purpose. {a} is mainly for ( ). {b} is mainly for ( ).\n"
        f"2) Where it sits. {a} is used at (layer or area). {b} is used at (layer or area).\n"
        f"3) What you see in troubleshooting. {a} problems look like ( ). {b} problems look like ( ).\n\n"
        "Quick example:\n"
        f"1) If you are trying to do (task), you probably care about {a}.\n"
        f"2) If you are trying to do (task), you probably care about {b}.\n"
    )
    return short, detailed


# -------------------------
# Draft recipes (offline)
# -------------------------
def draft_osi(topic: str):
    short = (
        "OSI model: A 7 layer way to break networking into steps.\n"
        "It helps you troubleshoot by narrowing down where the problem is."
    )
    detailed = (
        "OSI model (draft)\n\n"
        "Simple definition:\n"
        "1) It is a reference model that breaks networking into 7 layers.\n"
        "2) It helps you point to where a problem is happening.\n\n"
        "Layers (bottom to top):\n"
        "1) Physical. Cables, signals, Wi Fi radio.\n"
        "2) Data Link. MAC addresses, switching, local delivery.\n"
        "3) Network. IP and routing between networks.\n"
        "4) Transport. TCP or UDP, ports, reliability.\n"
        "5) Session. Connection and session handling.\n"
        "6) Presentation. Formatting and encryption.\n"
        "7) Application. HTTP, DNS, SMTP.\n"
    )
    return short, detailed


def draft_tcpip(topic: str):
    short = (
        "TCP IP model: The real internet stack.\n"
        "It is usually shown as 4 layers and it maps to OSI."
    )
    detailed = (
        "TCP IP model (draft)\n\n"
        "Simple definition:\n"
        "1) It is the practical model used on real networks.\n"
        "2) It groups networking into fewer layers than OSI.\n\n"
        "Common 4 layers:\n"
        "1) Link. Ethernet or Wi Fi.\n"
        "2) Internet. IP and routing.\n"
        "3) Transport. TCP or UDP plus ports.\n"
        "4) Application. HTTP, DNS, SMTP, SSH.\n"
    )
    return short, detailed


def draft_dns(topic: str):
    short = (
        "DNS: It turns names like google.com into an IP address.\n"
        "If DNS breaks, names stop working even if the internet is up."
    )
    detailed = (
        "DNS (draft)\n\n"
        "Simple definition:\n"
        "1) DNS maps domain names to IP addresses.\n\n"
        "Key points:\n"
        "1) Your device asks a resolver. That can be your router, ISP, or public DNS.\n"
        "2) Results are cached to speed things up.\n"
        "3) Bad DNS can make browsing slow or fail.\n"
    )
    return short, detailed


def draft_ports(topic: str):
    short = (
        "Ports: A number that tells your computer which program should get the network traffic.\n"
        "IP gets it to the device. Port gets it to the right app."
    )
    detailed = (
        "Ports (draft)\n\n"
        "Simple definition:\n"
        "1) Ports are numbers used with TCP or UDP.\n"
        "2) They help direct traffic to the right service on a device.\n\n"
        "Common examples:\n"
        "1) 80 is HTTP\n"
        "2) 443 is HTTPS\n"
        "3) 22 is SSH\n"
        "4) 53 is DNS\n"
    )
    return short, detailed


def draft_dhcp(topic: str):
    short = (
        "DHCP: It automatically gives devices an IP address and other network settings.\n"
        "Without it, you usually have to set IP info manually."
    )
    detailed = (
        "DHCP (draft)\n\n"
        "Simple definition:\n"
        "1) DHCP automatically assigns network settings.\n"
        "2) It usually gives IP address, subnet mask, gateway, and DNS.\n\n"
        "Troubleshooting hint:\n"
        "1) If you see 169.254.x.x, DHCP likely failed.\n"
    )
    return short, detailed


def draft_nat(topic: str):
    short = (
        "NAT: It lets many devices share one public IP on the internet.\n"
        "This is usually done by your router."
    )
    detailed = (
        "NAT (draft)\n\n"
        "Simple definition:\n"
        "1) NAT translates private IP addresses to a public IP for internet traffic.\n\n"
        "Key points:\n"
        "1) Many devices share one public IP.\n"
        "2) The router tracks connections so replies go to the right device.\n"
        "3) Port forwarding is often used for inbound connections.\n"
    )
    return short, detailed


def draft_subnet(topic: str):
    short = (
        "Subnet: A way to split IP networks into smaller parts.\n"
        "It helps decide what is local and what must be routed."
    )
    detailed = (
        "Subnet (draft)\n\n"
        "Simple definition:\n"
        "1) A subnet defines which IPs are local to each other.\n"
        "2) It is shown by a subnet mask or CIDR like /24.\n\n"
        "Example:\n"
        "1) 192.168.1.10 and 192.168.1.20 with /24 are on the same local network.\n"
    )
    return short, detailed


def draft_gateway(topic: str):
    short = (
        "Default gateway: The router your device uses to reach other networks.\n"
        "If it is wrong, local can work but internet fails."
    )
    detailed = (
        "Default gateway (draft)\n\n"
        "Simple definition:\n"
        "1) The gateway is the next hop router for traffic leaving your subnet.\n\n"
        "Key points:\n"
        "1) Local traffic stays in the subnet.\n"
        "2) Internet traffic goes to the gateway.\n"
    )
    return short, detailed


def draft_router_vs_switch(topic: str):
    short = (
        "Switch: moves traffic inside the same network using MAC addresses.\n"
        "Router: moves traffic between networks using IP routing."
    )
    detailed = (
        "Router vs Switch (draft)\n\n"
        "Simple definition:\n"
        "1) Switch is mostly Layer 2. It forwards frames by MAC.\n"
        "2) Router is Layer 3. It routes packets by IP.\n\n"
        "Key points:\n"
        "1) Switch connects devices on the same LAN.\n"
        "2) Router connects your LAN to other networks like the internet.\n"
    )
    return short, detailed


def smart_fallback(topic: str):
    short = (
        f"{topic}: I made a draft. It might be rough.\n"
        "If you want it better, teach it or ask for more detail."
    )
    detailed = (
        f"{topic} (draft)\n\n"
        "Simple definition:\n"
        "1) Write a clean 1 to 2 sentence definition here.\n\n"
        "Key points:\n"
        "1) Main point.\n"
        "2) Main point.\n"
        "3) Main point.\n\n"
        "Example:\n"
        "1) Short real world example.\n\n"
        "Common confusion:\n"
        "1) What people mix it up with.\n"
    )
    return short, detailed


def generate_drafts(topic: str) -> tuple[str, str]:
    comp = parse_compare_topic(topic)
    if comp:
        a, b = comp
        return draft_compare(topic, a, b)

    t = normalize_key_simple(topic)

    if "osi" in t and "model" in t:
        return draft_osi(topic)
    if "tcp" in t and "ip" in t:
        return draft_tcpip(topic)
    if t == "dns" or "domain name system" in t:
        return draft_dns(topic)
    if "port" in t or "ports" in t:
        return draft_ports(topic)
    if "dhcp" in t:
        return draft_dhcp(topic)
    if "nat" in t:
        return draft_nat(topic)
    if "subnet" in t or "cidr" in t or "subnet mask" in t:
        return draft_subnet(topic)
    if "gateway" in t:
        return draft_gateway(topic)
    if ("router" in t and "switch" in t) or "router vs switch" in t:
        return draft_router_vs_switch(topic)

    return smart_fallback(topic)


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
            "source": "offline_recipe_style_v3",
            "style_meta": {
                "max_sentence_words": style.get("max_sentence_words", 18),
                "samples": style.get("samples", []),
            },
        }

        wrote += 1
        print(f"Wrote draft: {topic}")

    # Save updated queue + drafts
    queue_data["queue"] = queue
    notes_data["drafts"] = drafts
    save_json(RESEARCH_QUEUE_PATH, queue_data)
    save_json(RESEARCH_NOTES_PATH, notes_data)

    if wrote == 0:
        print("No drafts written.")
    else:
        print(f"Done. Wrote {wrote} draft(s). Remaining in queue: {len(queue)}")


if __name__ == "__main__":
    main()

