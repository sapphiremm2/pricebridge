"""
state.py — Snooze tracking and pending approval management.

All state is persisted via storage.py (Redis or JSON files).
"""

import uuid
import time
from datetime import datetime, timedelta
import storage
from logger import log

# File keys
_SNOOZED        = "snoozed.json"
_SNOOZED_STOCK  = "snoozed_stock.json"
_PENDING        = "pending.json"
_PRICE_TRACK    = "price_tracking.json"
_ACTION_LOG     = "actions.log"


# ── Snooze (price alerts) ──────────────────────────────────────────────────────

def is_snoozed(item_key: str) -> bool:
    snoozed = storage.load(_SNOOZED)
    if item_key in snoozed:
        until = datetime.fromisoformat(snoozed[item_key])
        if datetime.now() < until:
            return True
        del snoozed[item_key]
        storage.save(_SNOOZED, snoozed)
    return False


def snooze(item_key: str, hours: int = 24) -> None:
    snoozed = storage.load(_SNOOZED)
    snoozed[item_key] = (datetime.now() + timedelta(hours=hours)).isoformat()
    storage.save(_SNOOZED, snoozed)


def clear_snoozed() -> None:
    storage.save(_SNOOZED, {})


# ── Snooze (stock alerts) ──────────────────────────────────────────────────────

def is_stock_snoozed(variant_id) -> bool:
    snoozed = storage.load(_SNOOZED_STOCK)
    key = str(variant_id)
    if key in snoozed:
        until = datetime.fromisoformat(snoozed[key])
        if datetime.now() < until:
            return True
        del snoozed[key]
        storage.save(_SNOOZED_STOCK, snoozed)
    return False


def snooze_stock(variant_id, hours: int = 24) -> None:
    snoozed = storage.load(_SNOOZED_STOCK)
    snoozed[str(variant_id)] = (datetime.now() + timedelta(hours=hours)).isoformat()
    storage.save(_SNOOZED_STOCK, snoozed)


def clear_snoozed_stock() -> None:
    storage.save(_SNOOZED_STOCK, {})


# ── Price stability tracking ───────────────────────────────────────────────────

def check_stable(item_key: str, sp_price: float, store_price: float,
                 stability_minutes: int, buffer: float) -> bool:
    """
    Returns True only after the price difference has been consistently present
    for at least `stability_minutes`, within ±`buffer` tolerance.
    """
    tracking = storage.load(_PRICE_TRACK)
    now      = datetime.now()

    if item_key in tracking:
        entry = tracking[item_key]
        sp_ok = abs(sp_price    - entry["sp_price"])    <= buffer
        st_ok = abs(store_price - entry["store_price"]) <= buffer

        if sp_ok and st_ok:
            elapsed = (now - datetime.fromisoformat(entry["first_seen"])).total_seconds() / 60
            return elapsed >= stability_minutes
        # Price shifted — reset the clock
        tracking[item_key] = _tracking_entry(sp_price, store_price, now)
    else:
        tracking[item_key] = _tracking_entry(sp_price, store_price, now)

    storage.save(_PRICE_TRACK, tracking)
    return False


def clear_tracking(item_key: str) -> None:
    tracking = storage.load(_PRICE_TRACK)
    if item_key in tracking:
        del tracking[item_key]
        storage.save(_PRICE_TRACK, tracking)


def clear_all_tracking() -> None:
    storage.save(_PRICE_TRACK, {})


def _tracking_entry(sp_price, store_price, now):
    return {"sp_price": sp_price, "store_price": store_price, "first_seen": now.isoformat()}


# ── Pending approvals ──────────────────────────────────────────────────────────

def new_approval_id() -> str:
    return f"{int(time.time())}_{uuid.uuid4().hex[:8]}"


def add_pending(approval_id: str, data: dict) -> None:
    pending = storage.load(_PENDING)
    data["created_at"] = datetime.now().isoformat()
    pending[approval_id] = data
    storage.save(_PENDING, pending)


def get_pending(approval_id: str) -> dict | None:
    return storage.load(_PENDING).get(approval_id)


def remove_pending(approval_id: str) -> None:
    pending = storage.load(_PENDING)
    if approval_id in pending:
        del pending[approval_id]
        storage.save(_PENDING, pending)


def has_pending_for(item_key: str) -> bool:
    pending = storage.load(_PENDING)
    return any(v.get("item_key") == item_key for v in pending.values())


def all_pending() -> dict:
    return storage.load(_PENDING)


def clear_pending() -> None:
    storage.save(_PENDING, {})


def pending_item_keys() -> set:
    return {v.get("item_key") for v in storage.load(_PENDING).values() if v.get("item_key")}


# ── Cleanup helpers ────────────────────────────────────────────────────────────

def cleanup_expired(max_pending_hours: int = 24, max_tracking_hours: int = 2) -> None:
    """Remove stale pending approvals and tracking entries."""
    now = datetime.now()

    # Stale pending
    pending  = storage.load(_PENDING)
    stale_p  = [
        k for k, v in pending.items()
        if not v.get("created_at") or
        (now - datetime.fromisoformat(v["created_at"])).total_seconds() > max_pending_hours * 3600
    ]
    if stale_p:
        for k in stale_p:
            del pending[k]
        storage.save(_PENDING, pending)
        log(f"[state] Removed {len(stale_p)} stale pending approvals")

    # Stale tracking
    tracking = storage.load(_PRICE_TRACK)
    stale_t  = [
        k for k, v in tracking.items()
        if (now - datetime.fromisoformat(v["first_seen"])).total_seconds() > max_tracking_hours * 3600
    ]
    if stale_t:
        for k in stale_t:
            del tracking[k]
        storage.save(_PRICE_TRACK, tracking)
        log(f"[state] Removed {len(stale_t)} stale tracking entries")

    # Expired snoozes
    for fname in [_SNOOZED, _SNOOZED_STOCK]:
        snoozed = storage.load(fname)
        expired = [k for k, v in snoozed.items() if datetime.fromisoformat(v) <= now]
        if expired:
            for k in expired:
                del snoozed[k]
            storage.save(fname, snoozed)
            log(f"[state] Removed {len(expired)} expired snoozes from {fname}")


# ── Action log ─────────────────────────────────────────────────────────────────

def log_action(action: str, item_name: str, username: str,
               old_price: float = None, new_price: float = None) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if action == "APPROVE":
        line = f"[{now}] APPROVED  {item_name:40s} ${old_price:.2f} → ${new_price:.2f}  by {username}"
    else:
        line = f"[{now}] DECLINED  {item_name}  by {username}"
    log(line)
    try:
        with open(_ACTION_LOG, "a") as f:
            f.write(line + "\n")
    except Exception as e:
        log(f"[state] Action log write error: {e}")
