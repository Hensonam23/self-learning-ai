#!/usr/bin/env python3

"""
VoiceSession: a thin bridge between the voice pipeline (STT/TTS)
and the core Machine Spirit brain.

Responsibilities:
- Receive recognized text from STT.
- Call brain.handle_message(...) with channel="voice".
- Pass the answer to a provided speak_fn (TTS or print).
- Avoid double-speak by blocking new requests while speaking.

This file does NOT depend on any specific STT/TTS library.
Whatever script handles audio can import VoiceSession and provide
a speak_fn that plays audio however it wants.
"""

from __future__ import annotations
from typing import Callable, Dict, Any, Optional

from brain import handle_message  # core brain entrypoint


SpeakFn = Callable[[str], None]


class VoiceSession:
    def __init__(
        self,
        speak_fn: SpeakFn,
        channel: str = "voice",
    ) -> None:
        """
        speak_fn: function that takes a string and "speaks" it
                  (TTS audio, printing to console, etc.).
        channel:  channel label for logging/insight ("voice" by default).
        """
        self.speak_fn = speak_fn
        self.channel = channel
        self._is_speaking = False

    @property
    def is_speaking(self) -> bool:
        return self._is_speaking

    def handle_text(self, user_text: str) -> Optional[Dict[str, Any]]:
        """
        Main entrypoint for voice code.

        Call this when STT has produced a final recognized text
        (usually AFTER the wake word has already been handled by
        your outer pipeline).

        Returns the full brain log entry dict, or None if it refused
        because we were still speaking.
        """
        user_text = user_text.strip()
        if not user_text:
            return None

        if self._is_speaking:
            # We're still speaking; ignore this to avoid double-speak.
            # Your outer loop can choose to buffer or drop it.
            return None

        # Ask the core brain for an answer, tagged as voice.
        entry = handle_message(user_text, channel=self.channel)

        # Speak synchronously. If your TTS is async, you can adapt:
        # e.g. have speak_fn start audio and then signal when done.
        answer = entry.get("answer", "")
        if answer:
            try:
                self._is_speaking = True
                self.speak_fn(answer)
            finally:
                self._is_speaking = False

        return entry
