#!/usr/bin/env python3

"""
Voice integration helpers for the Machine Spirit.

Goals:
- Voice and text both go through brain.handle_message().
- Keep wake-word logic (e.g., "machine spirit").
- Avoid double-speak: do not process new audio while speaking.

This file does NOT handle actual audio, STT, or TTS.
It gives you a clean controller that your voice loop can plug into.
"""

from dataclasses import dataclass
from typing import Optional

from brain import handle_message

# Wake words the STT transcript can contain
WAKE_WORDS = ("machine spirit", "machine-spirit")


@dataclass
class VoiceSession:
    """
    Tracks voice interaction state:

    - is_listening: whether we should process microphone input.
    - is_speaking: whether TTS is currently playing.
    - last_answer: last text answer spoken.

    Use this from your voice loop to avoid double-speak and keep
    behavior consistent.
    """
    is_listening: bool = True
    is_speaking: bool = False
    last_answer: Optional[str] = None

    # ---------------- state helpers ----------------

    def should_process_audio(self) -> bool:
        """
        Call this BEFORE sending audio/transcripts to the STT engine.

        If this returns False, you should ignore incoming audio (for example,
        while TTS is playing).
        """
        return self.is_listening and not self.is_speaking

    def start_speaking(self) -> None:
        """
        Call this RIGHT BEFORE you start TTS playback.
        """
        self.is_speaking = True
        self.is_listening = False

    def finish_speaking(self) -> None:
        """
        Call this RIGHT AFTER TTS playback finishes.
        """
        self.is_speaking = False
        self.is_listening = True

    # ---------------- wake-word + brain bridge ----------------

    def extract_command(self, transcript: str) -> Optional[str]:
        """
        Given a full STT transcript, check if it contains the wake word.

        Example transcripts:
          "machine spirit, what is my pc good for?"
          "hey machine spirit can you scan https://example.com"

        Returns the text AFTER the wake word, cleaned up.
        If no wake word is found, returns None.
        """
        text = transcript.strip()
        lower = text.lower()

        for wake in WAKE_WORDS:
            idx = lower.find(wake)
            if idx != -1:
                # Everything after the wake word is the actual command
                cmd = text[idx + len(wake):].strip(" ,.!?").strip()
                return cmd or None

        return None

    def handle_transcript(self, transcript: str) -> Optional[str]:
        """
        Main entry for your voice loop.

        - Takes the final STT transcript.
        - Checks for the wake word.
        - If present, strips it and sends the remainder through the Brain,
          using channel="voice".
        - Returns the answer text, or None if no wake word was detected.
        """
        cmd = self.extract_command(transcript)
        if not cmd:
            return None

        entry = handle_message(cmd, channel="voice")
        answer = entry["answer"]
        self.last_answer = answer
        return answer
