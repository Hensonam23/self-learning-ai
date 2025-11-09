import os
import json
from collections import deque

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CONTEXT_PATH = os.path.join(DATA_DIR, "context.json")


class MemoryManager:
    """
    Minimal per-channel short-term memory.

    - Keeps last `max_turns` for each channel (e.g. 'web', 'voice').
    - Persists to ./data/context.json so context survives restarts.
    """

    def __init__(self, max_turns=20, channels=None):
        self.max_turns = max_turns
        self.channels = channels or ["web", "voice"]
        self.contexts = {}
        self._ensure_data_dir()
        self._load_from_disk()
        self._ensure_channels()

    def _ensure_data_dir(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
        except Exception:
            pass

    def _ensure_channels(self):
        for ch in self.channels:
            if ch not in self.contexts:
                self.contexts[ch] = deque(maxlen=self.max_turns)
            else:
                # Normalize maxlen in case file had different size
                self.contexts[ch] = deque(self.contexts[ch], maxlen=self.max_turns)

    def _load_from_disk(self):
        if not os.path.exists(CONTEXT_PATH):
            return
        try:
            with open(CONTEXT_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            return

        if not isinstance(raw, dict):
            return

        for channel, turns in raw.items():
            dq = deque(maxlen=self.max_turns)
            if isinstance(turns, list):
                for t in turns:
                    if isinstance(t, dict) and "user" in t and "ai" in t:
                        dq.append({
                            "user": str(t["user"]),
                            "ai": str(t["ai"])
                        })
            self.contexts[channel] = dq

    def _save_to_disk(self):
        serializable = {
            ch: list(dq) for ch, dq in self.contexts.items()
        }
        try:
            with open(CONTEXT_PATH, "w", encoding="utf-8") as f:
                json.dump(serializable, f, ensure_ascii=False, indent=2)
        except Exception:
            # Never crash server on write issues
            pass

    def add_turn(self, channel: str, user_message: str, ai_response: str):
        """Store one user/AI exchange for a channel."""
        if channel not in self.contexts:
            self.contexts[channel] = deque(maxlen=self.max_turns)

        self.contexts[channel].append({
            "user": user_message,
            "ai": ai_response,
        })
        self._save_to_disk()

    def get_context(self, channel: str):
        """Return list of recent turns for a channel."""
        if channel not in self.contexts:
            self.contexts[channel] = deque(maxlen=self.max_turns)
        return list(self.contexts[channel])
