"""
storage.py — Thin persistence layer.

Priority: Upstash Redis REST API → local JSON files.
Switching between them is transparent to the rest of the app.
"""

import json
import os
import threading
import requests
from config import UPSTASH_REST_URL, UPSTASH_REST_TOKEN
from logger import log

_file_lock = threading.Lock()


# ── Low-level Redis helpers ────────────────────────────────────────────────────

def _redis_get(key: str):
    if not UPSTASH_REST_URL:
        return None
    try:
        r = requests.get(
            f"{UPSTASH_REST_URL}/get/{key}",
            headers={"Authorization": f"Bearer {UPSTASH_REST_TOKEN}"},
            timeout=5,
        )
        if r.status_code == 200:
            return r.json().get("result")
    except Exception as e:
        log(f"[storage] Redis GET error ({key}): {e}")
    return None


def _redis_set(key: str, value: str) -> bool:
    if not UPSTASH_REST_URL:
        return False
    try:
        r = requests.post(
            UPSTASH_REST_URL,
            headers={
                "Authorization": f"Bearer {UPSTASH_REST_TOKEN}",
                "Content-Type": "application/json",
            },
            json=["SET", key, value],
            timeout=5,
        )
        return r.status_code == 200
    except Exception as e:
        log(f"[storage] Redis SET error ({key}): {e}")
    return False


# ── Public API ─────────────────────────────────────────────────────────────────

def load(filename: str, default=None):
    """Load a JSON document. Tries Redis first, then local file."""
    if default is None:
        default = {}

    raw = _redis_get(f"monitor:{filename}")
    if raw:
        try:
            return json.loads(raw)
        except Exception as e:
            log(f"[storage] JSON parse error ({filename}): {e}")

    with _file_lock:
        if os.path.exists(filename):
            try:
                with open(filename) as f:
                    return json.load(f)
            except Exception as e:
                log(f"[storage] File read error ({filename}): {e}")

    return default


def save(filename: str, data) -> None:
    """Persist a JSON document. Writes to both Redis and local file."""
    serialised = json.dumps(data)

    if UPSTASH_REST_URL:
        _redis_set(f"monitor:{filename}", serialised)

    with _file_lock:
        try:
            with open(filename, "w") as f:
                f.write(serialised)
        except Exception as e:
            log(f"[storage] File write error ({filename}): {e}")
