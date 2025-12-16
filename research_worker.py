from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone


APP_ROOT = Path(__file__).resolve().parent
RESEARCH_QUEUE_PATH = APP_ROOT / "data" / "research_queue.json"
RESEARCH_NOTES_PATH = APP_ROOT / "data" / "research_notes.json"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_key_simple(text: str) -> str:
    key = (text or "").strip().lower()
    while key.endswith("?"):
        key = key[:-1].strip()
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
    parts = [p.strip() for p in raw.split() if p.strip()]
    stop = {"the", "a", "an", "and", "or", "to", "of", "in", "for", "on", "with", "is", "are"}
    parts = [p for p in parts if p not in stop]
    out = []
    for p in parts:
        if p not in out:
            out.append(p)
    return out[:12]


# --------- Offline knowledge recipes (hand-built) ---------
def draft_for_osi_model(topic: str):
    short = (
        "OSI model: A 7-layer way to break networking into chunks so it's easier to understand and troubleshoot.\n"
        "It goes from physical stuff (cables) up to apps (web, email)."
    )

    detailed = (
        "OSI model (draft)\n\n"
        "Simple definition:\n"
        "- The OSI model is a 7-layer reference model that breaks network communication into steps.\n"
        "- It helps you describe where a problem is happening (cable vs IP vs application).\n\n"
        "Layers (bottom to top):\n"
        "1) Physical: cables, signals, Wi-Fi radio\n"
        "2) Data Link: MAC addresses, switches, local delivery\n"
        "3) Network: IP addressing + routing between networks\n"
        "4) Transport: TCP/UDP, ports, reliable delivery\n"
        "5) Session: managing sessions / connections (concept layer)\n"
        "6) Presentation: formatting, encryption, compression (concept layer)\n"
        "7) Application: what the user uses (HTTP, DNS, SMTP, etc)\n\n"
        "Real-world example:\n"
        "- If Wi-Fi is connected but websites won’t load, Physical/Data Link might be fine, but Network/DNS/Application could be the issue.\n\n"
        "Common confusion:\n"
        "- OSI is a model for understanding. The real-world TCP/IP stack is usually described as 4 or 5 layers."
    )
    return short, detailed


def draft_for_tcp_ip(topic: str):
    short = (
        "TCP/IP model: The practical internet model (usually 4 layers) used in real networks.\n"
        "It maps roughly to OSI, but fewer layers."
    )
    detailed = (
        "TCP/IP model (draft)\n\n"
        "Simple definition:\n"
        "- TCP/IP is the real-world networking model used for the internet.\n"
        "- It groups networking into fewer layers than OSI.\n\n"
        "Common 4 layers:\n"
        "1) Link: Ethernet/Wi-Fi, MAC, switching\n"
        "2) Internet: IP + routing\n"
        "3) Transport: TCP/UDP + ports\n"
        "4) Application: HTTP, DNS, SMTP, SSH, etc\n\n"
        "Real-world example:\n"
        "- Ping tests IP (Internet layer). Curl/browser tests Application layer.\n"
    )
    return short, detailed


def draft_for_dns(topic: str):
    short = (
        "DNS: The internet's phonebook. It turns names like google.com into IP addresses.\n"
        "If DNS is broken, the internet can 'work' but websites won't load by name."
    )
    detailed = (
        "DNS (draft)\n\n"
        "Simple definition:\n"
        "- DNS (Domain Name System) translates domain names into IP addresses.\n\n"
        "Key points:\n"
        "- Your device asks a DNS resolver (often your router or ISP or public DNS).\n"
        "- Results can be cached to speed things up.\n"
        "- Wrong DNS can cause slow browsing or failed websites.\n\n"
        "Real-world example:\n"
        "- If you can ping 8.8.8.8 but not google.com, DNS is likely the problem.\n"
    )
    return short, detailed


def draft_for_ip_address(topic: str):
    short = (
        "IP address: A number that identifies a device on a network (like a mailing address).\n"
        "IPv4 looks like 192.168.1.10, IPv6 is longer."
    )
    detailed = (
        "IP address (draft)\n\n"
        "Simple definition:\n"
        "- An IP address identifies a device and helps route traffic to it.\n\n"
        "Key points:\n"
        "- Private IPs (like 192.168.x.x) are used inside your home network.\n"
        "- Public IP is what the internet sees (usually from your ISP).\n"
        "- Subnet masks / CIDR decide what is local vs routed.\n\n"
        "Real-world example:\n"
        "- Two devices on the same Wi-Fi usually have different private IPs, but share one public IP through NAT."
    )
    return short, detailed


def draft_for_ports(topic: str):
    short = (
        "Ports: Numbers used to direct network traffic to the right program on a device.\n"
        "Example: 80/443 for web, 22 for SSH."
    )
    detailed = (
        "Ports (draft)\n\n"
        "Simple definition:\n"
        "- A port is a number used with TCP or UDP so the OS knows which application should get the traffic.\n\n"
        "Key points:\n"
        "- IP gets traffic to the device. Ports get it to the right app on that device.\n"
        "- TCP is connection-based (reliable). UDP is faster but no built-in reliability.\n\n"
        "Examples:\n"
        "- 80 = HTTP, 443 = HTTPS\n"
        "- 22 = SSH\n"
        "- 53 = DNS\n"
    )
    return short, detailed


def smart_fallback(topic: str):
    short = (
        f"{topic}: A draft explanation is being built.\n"
        "If you want it better, ask for more detail or teach a better definition."
    )
    detailed = (
        f"{topic} (draft)\n\n"
        "Simple definition:\n"
        f"- {topic} means: (write a clean 1–2 sentence definition here)\n\n"
        "Key points:\n"
        "- Point 1: (main idea)\n"
        "- Point 2: (main idea)\n"
        "- Point 3: (main idea)\n\n"
        "Real-world example:\n"
        "- (short example that makes it click)\n\n"
        "Common confusion:\n"
        "- (what people mix it up with)\n"
    )
    return short, detailed


def generate_drafts(topic: str) -> tuple[str, str]:
    t = normalize_key_simple(topic)

    if "osi" in t and "model" in t:
        return draft_for_osi_model(topic)
    if "tcp/ip" in t or "tcp ip" in t or ("tcp" in t and "ip" in t and "model" in t):
        return draft_for_tcp_ip(topic)
    if t == "dns" or "domain name system" in t:
        return draft_for_dns(topic)
    if "ip address" in t or t == "ip":
        return draft_for_ip_address(topic)
    if "port" in t or "ports" in t:
        return draft_for_ports(topic)

    return smart_fallback(topic)


def main() -> None:
    ensure_files_exist()

    raw_queue = load_json(RESEARCH_QUEUE_PATH, {"queue": []})
    queue_data = coerce_queue_structure(raw_queue)

    raw_notes = load_json(RESEARCH_NOTES_PATH, {"drafts": {}})
    notes_data = coerce_notes_structure(raw_notes)

    queue = queue_data["queue"]
    if not queue:
        print("No pending research tasks. Queue is clear.")
        return

    task = queue.pop(0)
    queue_data["queue"] = queue
    save_json(RESEARCH_QUEUE_PATH, queue_data)

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

    draft = {
        "topic": topic,
        "key": key,
        "keywords": ks,
        "short": short,
        "detailed": detailed,
        "created_at": utc_now_iso(),
        "source": "offline_recipe_v1",
    }

    drafts = notes_data["drafts"]
    drafts[key] = draft
    notes_data["drafts"] = drafts
    save_json(RESEARCH_NOTES_PATH, notes_data)

    print(f"Wrote improved draft for: {topic}")


if __name__ == "__main__":
    main()
