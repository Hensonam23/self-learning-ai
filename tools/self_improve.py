#!/usr/bin/env python3
"""Orchestrate automatic self-improvement.

Runs the error-based task generator followed by the code updater.
"""
from __future__ import annotations
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(script: str) -> None:
    """Helper to invoke another script with current interpreter."""
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", script)], check=False)


def main() -> None:
    run("error_task_generator.py")
    run("code_updater.py")


if __name__ == "__main__":
    main()
