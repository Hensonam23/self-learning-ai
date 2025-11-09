#!/usr/bin/env python3
# tools/autoimprove.py
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path.home() / "self-learning-ai"
VENV = REPO / "venv" / "bin"
PY = str((REPO / "venv" / "bin" / "python3"))
PIP = str((REPO / "venv" / "bin" / "pip"))
LOG = REPO / "logs" / "autoimprove.log"

DEV_PKGS = [
    "black==24.4.2",
    "isort==5.13.2",
    "flake8==7.1.0",
    "bandit==1.7.9",
]


def sh(cmd, cwd=REPO, check=True):
    return subprocess.run(cmd, cwd=cwd, check=check, text=True, capture_output=True)


def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts}  {msg}\n"
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line)
    print(line, end="")


def ensure_dev_tools():
    try:
        sh([PY, "-m", "pip", "install", "--upgrade", "pip", "wheel"])
        sh([PIP, "install", *DEV_PKGS])
    except subprocess.CalledProcessError as e:
        log(f"[AUTOIMPROVE] pip install failed: {e.stderr or e.stdout}")


def git_dirty():
    r = sh(["git", "status", "--porcelain"], check=False)
    return bool(r.stdout.strip())


def main():
    os.chdir(REPO)
    log("[AUTOIMPROVE] start")
    ensure_dev_tools()

    # Format/imports (safe/mechanical)
    for cmd in (
        [PY, "-m", "black", "."],
        [PY, "-m", "isort", "."],
    ):
        r = sh(cmd, check=False)
        if r.returncode != 0:
            log(f"[AUTOIMPROVE] cmd failed: {' '.join(cmd)}\n{r.stdout}\n{r.stderr}")

    # Lint & security checks (don’t fail run; just report)
    for name, cmd in (
        ("flake8", [PY, "-m", "flake8", "."]),
        ("bandit", [PY, "-m", "bandit", "-q", "-r", "."]),
    ):
        r = sh(cmd, check=False)
        summary = (r.stdout or r.stderr or "").strip()[:4000]
        log(f"[AUTOIMPROVE] {name} report:\n{summary or '(clean)'}")

    # Commit/push only if mechanical changes present
    if not git_dirty():
        log("[AUTOIMPROVE] no code changes")
        return

    # Create branch
    ts = time.strftime("%Y%m%d-%H%M")
    branch = f"autoimprove/{ts}"
    sh(["git", "checkout", "-B", branch], check=False)
    sh(["git", "add", "-A"], check=False)
    msg = "chore(autoimprove): format/imports + lint/security snapshot"
    sh(["git", "commit", "-m", msg], check=False)

    # Push (SSH origin must be set)
    pr_hint = "(install GitHub CLI `gh auth login` to auto-open PR)"
    try:
        sh(["git", "push", "-u", "origin", branch], check=False)
        log(f"[AUTOIMPROVE] pushed branch {branch}")
        # Try PR via gh if present
        try:
            gh = subprocess.run(["gh", "--version"], capture_output=True, text=True)
            if gh.returncode == 0:
                pr = sh(
                    [
                        "gh",
                        "pr",
                        "create",
                        "--title",
                        msg,
                        "--body",
                        "Automated maintenance PR (format/imports/lints).",
                        "--label",
                        "autoimprove,bot",
                        "--draft",
                    ],
                    check=False,
                )
                if pr.returncode == 0:
                    log("[AUTOIMPROVE] PR opened (draft).")
                else:
                    log(f"[AUTOIMPROVE] PR attempt failed:\n{pr.stdout}\n{pr.stderr}")
            else:
                log(f"[AUTOIMPROVE] {pr_hint}")
        except FileNotFoundError:
            log(f"[AUTOIMPROVE] {pr_hint}")
    except subprocess.CalledProcessError as e:
        log(f"[AUTOIMPROVE] push failed: {e.stderr or e.stdout}")

    log("[AUTOIMPROVE] done")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"[AUTOIMPROVE] crash: {e}")
        sys.exit(1)
