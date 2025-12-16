from __future__ import annotations

import json
import sys
import time
import subprocess
from pathlib import Path
from datetime import datetime

APP_ROOT = Path(__file__).resolve().parent
RESEARCH_QUEUE_PATH = APP_ROOT / "data" / "research_queue.json"
LOG_PATH = APP_ROOT / "data" / "night_learner.log"


def load_queue_len() -> int:
    if not RESEARCH_QUEUE_PATH.exists():
        return 0
    try:
        data = json.loads(RESEARCH_QUEUE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return 0

    if isinstance(data, dict):
        q = data.get("queue", [])
        return len(q) if isinstance(q, list) else 0
    if isinstance(data, list):
        return len(data)
    return 0


def log_line(msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    LOG_PATH.write_text("", encoding="utf-8") if not LOG_PATH.exists() else None
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")


def parse_int(value: str, default: int, min_v: int, max_v: int) -> int:
    try:
        n = int(value)
        if n < min_v:
            return default
        if n > max_v:
            return max_v
        return n
    except Exception:
        return default


def main() -> None:
    # Usage:
    #   python3 night_learner.py
    #   python3 night_learner.py 5
    #   python3 night_learner.py 10 1
    # args: [batch_size] [sleep_seconds]
    batch_size = 5
    sleep_seconds = 0

    if len(sys.argv) >= 2:
        batch_size = parse_int(sys.argv[1], default=5, min_v=1, max_v=25)
    if len(sys.argv) >= 3:
        sleep_seconds = parse_int(sys.argv[2], default=0, min_v=0, max_v=3600)

    log_line(f"Night learner start. batch_size={batch_size}, sleep_seconds={sleep_seconds}")

    loops = 0
    max_loops = 200  # safety limit

    while True:
        remaining = load_queue_len()
        if remaining <= 0:
            print("Night learner: queue is empty. Done.")
            log_line("Queue empty. Done.")
            break

        loops += 1
        if loops > max_loops:
            print("Night learner: hit safety loop limit. Stopping.")
            log_line("Hit safety loop limit. Stopping.")
            break

        print(f"Night learner: queue has {remaining} item(s). Running worker batch={batch_size}")
        log_line(f"Queue={remaining}. Running worker batch={batch_size}")

        # Run the worker using the same python you launched this script with
        cmd = [sys.executable, str(APP_ROOT / "research_worker.py"), str(batch_size)]
        result = subprocess.run(cmd, capture_output=True, text=True)

        # Print worker output to console
        if result.stdout.strip():
            print(result.stdout.strip())
        if result.stderr.strip():
            print(result.stderr.strip())

        # Log a short summary
        log_line(f"Worker returncode={result.returncode}")

        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    log_line("Night learner end.")


if __name__ == "__main__":
    main()
