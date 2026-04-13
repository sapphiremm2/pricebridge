"""logger.py — Timestamped console logger."""

from datetime import datetime


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)
