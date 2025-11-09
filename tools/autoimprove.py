#!/usr/bin/env python3
from __future__ import annotations
import os, subprocess, sys, time, pathlib, shlex

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOGS = ROOT / "logs"
LOGS.mkdir(parents=True, exist_ok=True)

def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts}  [AUTOIMPROVE] {msg}"
    print(line, flush=True)
    with open(LOGS / "autoimprove.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")

def run(cmd: list[str], timeout: int | None = None, capture_file: pathlib.Path | None = None, check_ok: bool = False) -> int:
    log(f"$ {' '.join(shlex.quote(c) for c in cmd)}")
    try:
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        log(f"TIMEOUT: {' '.join(cmd)}")
        return 124
    if capture_file:
        capture_file.write_text((p.stdout or "") + ("\n--- STDERR ---\n" + (p.stderr or "")), encoding="utf-8")
    if check_ok and p.returncode != 0:
        log(f"fatal: Command '{cmd[0]}' returned {p.returncode}.")
        sys.exit(1)
    return p.returncode

def git_changed() -> bool:
    return subprocess.run(["git", "diff", "--quiet"], cwd=str(ROOT)).returncode != 0

def main():
    log("start")
    # ensure git repo & branch
    run(["git", "rev-parse", "--is-inside-work-tree"], check_ok=True)
    branch = time.strftime("autoimprove/%Y%m%d-%H%M")
    run(["git", "checkout", "-B", branch], check_ok=True)

    # formatters
    run(["autoflake", "-r", "--in-place", "--remove-all-unused-imports", "--remove-unused-variables",
         "--exclude", "venv,.venv,*.pyc,__pycache__", "."], timeout=120)
    run(["isort", "--profile", "black", "."], timeout=120)
    rc_black = run(["black", "."], timeout=180)
    if rc_black not in (0, 123):  # 123 → nothing changed/some paths invalid; allow pass
        log("fatal: black failed")
        sys.exit(1)

    # linters (always continue; just write reports)
    run(["flake8"], timeout=180, capture_file=LOGS / "flake8.txt")
    run(["bandit", "-q", "-r", ".", "-x", "venv,.venv,tests,**/site-packages,**/.venv,**/venv"], timeout=240, capture_file=LOGS / "bandit.txt")

    # smoke test (compile sources)
    rc_smoke = run([sys.executable, str(ROOT / "tools" / "smoke.py")], timeout=120)
    if rc_smoke != 0:
        log("smoke failed; aborting commit")
        sys.exit(0)  # don't fail the service; just skip commit

    # commit & optional push
    if git_changed():
        msg = "autoimprove: format/lint + smoke"
        run(["git", "add", "-A"])
        run(["git", "commit", "-m", msg])
        if os.environ.get("AUTO_PUSH", "1") == "1":
            run(["git", "push", "-u", "origin", branch])
        log("committed (and pushed if configured)")
    else:
        log("no code changes")

if __name__ == "__main__":
    main()
