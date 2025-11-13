#!/usr/bin/env python3

"""
Diagnostic voice loop for the Machine Spirit.

This does NOT use a real microphone or speaker.
Instead, it simulates:

- STT: you type text after the wake word.
- TTS: it prints the answer prefixed with [VOICE].

All logic goes through VoiceSession, which calls the core brain.
"""

from __future__ import annotations
from typing import Optional

from voice_session import VoiceSession


def console_speak(text: str) -> None:
    """
    Fake TTS: just print the answer to the console.
    """
    print(f"[VOICE] {text}")


def main() -> None:
    print("Machine Spirit voice diagnostic online.")
    print("Type what the STT would have recognized (without wake word).")
    print("Type 'exit' or 'quit' to stop.\n")

    session = VoiceSession(speak_fn=console_speak, channel="voice")

    while True:
        try:
            user_text = input("You (voice): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nShutting down voice diagnostic.")
            break

        if not user_text:
            continue

        if user_text.lower() in ("exit", "quit"):
            print("Shutting down voice diagnostic.")
            break

        entry: Optional[dict] = session.handle_text(user_text)
        if entry is None:
            print("[voice] Busy speaking; input ignored.")
        # You could also inspect entry here if you want:
        # print(entry)
        # but it's not necessary for normal use.


if __name__ == "__main__":
    main()
