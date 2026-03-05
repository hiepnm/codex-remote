import os
import json
import signal
import subprocess
import sys
import time
from urllib import request
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

WATCH_FILES = {".env", "bot_worker.py", "README.md"}
WATCH_EXTS = {".py"}
POLL_SECONDS = 0.8
IGNORED_DIRS = {".git", "__pycache__", ".venv", "venv"}
BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
ALLOWED_CHAT_ID = os.environ.get("TG_ALLOWED_CHAT_ID", "").strip()
BOT_NAME = os.environ.get("BOT_NAME", "codex-remote").strip() or "codex-remote"


def list_watch_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    for p in root.rglob("*"):
        if any(part in IGNORED_DIRS for part in p.parts):
            continue
        if p.is_dir():
            continue
        if p.name in WATCH_FILES or p.suffix in WATCH_EXTS:
            paths.append(p)
    return sorted(set(paths))


def snapshot(paths: list[Path]) -> dict[Path, float]:
    out: dict[Path, float] = {}
    for p in paths:
        try:
            out[p] = p.stat().st_mtime
        except FileNotFoundError:
            out[p] = -1.0
    return out


def has_changed(prev: dict[Path, float], curr: dict[Path, float]) -> bool:
    keys = set(prev) | set(curr)
    for k in keys:
        if prev.get(k, -1.0) != curr.get(k, -1.0):
            return True
    return False


def start_worker() -> subprocess.Popen[str]:
    print("[dev-runner] starting bot_worker.py", flush=True)
    return subprocess.Popen([sys.executable, "bot_worker.py"], text=True)


def stop_worker(proc: subprocess.Popen[str]):
    if proc.poll() is not None:
        return
    print("[dev-runner] stopping bot_worker.py", flush=True)
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def notify_telegram(text: str):
    if not BOT_TOKEN or not ALLOWED_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = json.dumps({"chat_id": ALLOWED_CHAT_ID, "text": text}).encode("utf-8")
    req = request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with request.urlopen(req, timeout=8) as _:
            pass
    except Exception:
        # Keep shutdown path resilient even if Telegram is temporarily unavailable.
        pass


def main():
    root = Path.cwd()
    proc = start_worker()
    prev = snapshot(list_watch_paths(root))

    def handle_stop(_sig, _frame):
        stop_worker(proc)
        print("[dev-runner] stopped", flush=True)
        notify_telegram(f"🛑 {BOT_NAME} dev-runner stopped")
        raise SystemExit(0)

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    while True:
        time.sleep(POLL_SECONDS)
        paths = list_watch_paths(root)
        curr = snapshot(paths)
        if has_changed(prev, curr):
            print("[dev-runner] change detected, restarting...", flush=True)
            stop_worker(proc)
            proc = start_worker()
            prev = curr
            continue
        prev = curr
        if proc.poll() is not None:
            print("[dev-runner] bot exited, restarting...", flush=True)
            proc = start_worker()


if __name__ == "__main__":
    main()
