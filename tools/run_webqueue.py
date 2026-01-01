#!/usr/bin/env python3
import os
import sys

# This script runs the brain in a non-interactive way and triggers webqueue.
# It is meant to be used by systemd timers.

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

def main():
    try:
        import brain  # brain.py in project root
    except Exception as e:
        print(f"run_webqueue: failed to import brain.py: {e}")
        return 2

    try:
        state = brain.BrainState()
        state.run_auto_import()
        state.run_auto_ingest()

        done, attempted = state.webqueue(limit=brain.WEBQUEUE_LIMIT_PER_RUN)

        # Save in case anything changed
        state.save_all()

        print(f"run_webqueue: learned={done} attempted={attempted} limit={brain.WEBQUEUE_LIMIT_PER_RUN}")
        return 0
    except Exception as e:
        print(f"run_webqueue: error: {e}")
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
