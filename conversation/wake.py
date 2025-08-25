from __future__ import annotations
import threading, time, re
from typing import Callable, List, Optional

class WakeConversationManager:
    """
    Simple wake-word + silence-finalizer gate.

    Flow:
      - Idle until a wake word appears in an utterance.
      - When woken, buffer text and restart a 3s finalize timer on each utterance.
      - When the timer fires, call on_command(buffered_text).
      - If 20s pass with no utterances, go back to idle.
    """

    def __init__(
        self,
        wake_words: List[str],
        on_command: Callable[[str], None],
        push_ai_caption: Callable[[str], None],
        silence_final_ms: int = 3000,
        idle_timeout_s: int = 20,
    ):
        self.wake_words = [w.lower().strip() for w in wake_words]
        self.on_command = on_command
        self.push_ai_caption = push_ai_caption

        self.silence_final_ms = max(500, int(silence_final_ms))
        self.idle_timeout_s = max(5, int(idle_timeout_s))

        self._awake = False
        self._buffer = ""
        self._final_t: Optional[threading.Timer] = None
        self._idle_t: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    # -------- public API --------
    def on_utterance(self, text: str) -> None:
        if not text:
            return
        s = text.strip()
        sl = s.lower()

        with self._lock:
            if not self._awake:
                if self._contains_wake(sl):
                    self._awake = True
                    rest = self._strip_wake(s).strip(" ,.:;!?")
                    self.push_ai_caption("Listening.")
                    if rest:
                        self._append(rest)
                    self._arm_finalize()
                    self._arm_idle()
                else:
                    # ignore non-wake speech while idle
                    return
            else:
                self._append(s)
                self._arm_finalize()
                self._arm_idle()

    def shutdown(self):
        with self._lock:
            if self._final_t:
                self._final_t.cancel()
                self._final_t = None
            if self._idle_t:
                self._idle_t.cancel()
                self._idle_t = None
            self._awake = False
            self._buffer = ""

    # -------- internals --------
    def _contains_wake(self, sl: str) -> bool:
        return any(w in sl for w in self.wake_words)

    def _strip_wake(self, s: str) -> str:
        sl = s.lower()
        for w in self.wake_words:
            idx = sl.find(w)
            if idx != -1:
                # remove the wake phrase and anything like a trailing comma
                before = s[:idx]
                after = s[idx + len(w):]
                return (before + " " + after).strip()
        return s

    def _append(self, s: str) -> None:
        if not s:
            return
        self._buffer = (self._buffer + " " + s).strip()

    def _arm_finalize(self):
        if self._final_t:
            self._final_t.cancel()
        self._final_t = threading.Timer(self.silence_final_ms / 1000.0, self._finalize)
        self._final_t.daemon = True
        self._final_t.start()

    def _arm_idle(self):
        if self._idle_t:
            self._idle_t.cancel()
        self._idle_t = threading.Timer(self.idle_timeout_s, self._go_idle)
        self._idle_t.daemon = True
        self._idle_t.start()

    def _finalize(self):
        with self._lock:
            text = self._buffer.strip()
            self._buffer = ""
        if not text:
            return
        # run the command in background so we never block audio
        threading.Thread(target=self.on_command, args=(text,), daemon=True).start()

    def _go_idle(self):
        with self._lock:
            self._awake = False
            self._buffer = ""
        self.push_ai_caption("Standing by.")
