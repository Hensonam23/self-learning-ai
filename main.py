#!/usr/bin/env python3

from brain import brain, handle_message
from style_manager import StyleManager

def main():
    print("Machine Spirit (main) online. Type a message, Ctrl+C to exit.")
    while True:
        try:
            user_text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nShutting down.")
            break

        if not user_text:
            continue

        entry = handle_message(user_text, channel="cli")
        print(entry["answer"])


if __name__ == "__main__":
    main()
