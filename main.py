"""
main.py — Flask server, Discord interaction handler, and startup.

Endpoints:
  GET  /                   → health check
  POST /interactions        → Discord button interactions (signed)
  GET  /reset?key=          → clear pending + prices + tracking
  GET  /resetstock?key=     → clear stock snoozes
  GET  /resettracking?key=  → clear price stability tracking
"""

import threading
import time
from functools import wraps
from flask import Flask, request, jsonify
from nacl.signing import VerifyKey

import state
import alerts
import gateway
import storage
import checker
from updater import update_price
from config import DISCORD_PUBLIC_KEY, API_SECRET, PORT, CHECK_INTERVAL_SECS, STORE_ADAPTER
from logger import log

app = Flask(__name__)


# ── Auth decorator ─────────────────────────────────────────────────────────────

def require_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if API_SECRET and request.args.get("key") != API_SECRET:
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


# ── Health ─────────────────────────────────────────────────────────────────────

@app.route("/")
def health():
    return jsonify({"status": "ok", "service": "mm2-price-monitor"})


# ── Admin HTTP endpoints ───────────────────────────────────────────────────────

@app.route("/reset")
@require_key
def reset():
    state.clear_pending()
    storage.save("prices.json", {})
    state.clear_all_tracking()
    log("HTTP /reset: prices + pending + tracking cleared")
    return jsonify({"status": "ok"})


@app.route("/resetstock")
@require_key
def resetstock():
    state.clear_snoozed_stock()
    log("HTTP /resetstock: stock snoozes cleared")
    return jsonify({"status": "ok"})


@app.route("/resettracking")
@require_key
def resettracking():
    state.clear_all_tracking()
    log("HTTP /resettracking: price stability tracking cleared")
    return jsonify({"status": "ok"})


# ── Discord interactions ───────────────────────────────────────────────────────

@app.route("/interactions", methods=["POST"])
def interactions():
    # Verify Ed25519 signature
    sig       = request.headers.get("X-Signature-Ed25519")
    timestamp = request.headers.get("X-Signature-Timestamp")
    raw_body  = request.data

    if DISCORD_PUBLIC_KEY and sig and timestamp:
        try:
            VerifyKey(bytes.fromhex(DISCORD_PUBLIC_KEY)).verify(
                f"{timestamp}{raw_body.decode()}".encode(),
                bytes.fromhex(sig),
            )
        except Exception:
            return "Invalid signature", 401
    else:
        return "Missing signature data", 401

    data = request.json
    if not data:
        return "Bad request", 400

    # PING
    if data.get("type") == 1:
        return jsonify({"type": 1})

    # Button interaction
    if data.get("type") == 3:
        custom_id = data.get("data", {}).get("custom_id", "")

        if custom_id.startswith("approve_"):
            return _handle_approve(custom_id[len("approve_"):], data)

        if custom_id.startswith("decline_"):
            return _handle_decline(custom_id[len("decline_"):], data)

        if custom_id.startswith("stock_snooze_"):
            return _handle_stock_snooze(custom_id[len("stock_snooze_"):], data)

    return jsonify({"type": 4, "data": {"content": "Unknown interaction", "flags": 64}})


def _user_info(interaction):
    user = interaction.get("member", {}).get("user", {})
    return user.get("id", ""), user.get("username", "Unknown")


def _has_permission(interaction) -> bool:
    from config import DISCORD_ALLOWED_ROLES
    if not DISCORD_ALLOWED_ROLES:
        return True
    roles = interaction.get("member", {}).get("roles", [])
    return any(r in DISCORD_ALLOWED_ROLES for r in roles)


def _handle_approve(approval_id: str, interaction) -> any:
    if not _has_permission(interaction):
        return jsonify({"type": 4, "data": {"content": "No permission.", "flags": 64}})

    pending    = state.get_pending(approval_id)
    user_id, username = _user_info(interaction)
    msg_id     = interaction.get("message", {}).get("id")
    channel_id = interaction.get("channel_id")

    if not pending:
        if msg_id:
            alerts.delete_message(channel_id, msg_id)
        return jsonify({"type": 6})

    if update_price(pending["variant_id"], pending["new_price"]):
        state.remove_pending(approval_id)
        state.log_action("APPROVE", pending["name"], username, pending["old_price"], pending["new_price"])
        alerts.send_approved(channel_id, msg_id, pending["name"],
                              pending["old_price"], pending["new_price"], username)
        return jsonify({"type": 6})

    return jsonify({"type": 4, "data": {"content": "Shopify update failed — check API credentials.", "flags": 64}})


def _handle_decline(approval_id: str, interaction) -> any:
    if not _has_permission(interaction):
        return jsonify({"type": 4, "data": {"content": "No permission.", "flags": 64}})

    pending    = state.get_pending(approval_id)
    user_id, username = _user_info(interaction)
    msg_id     = interaction.get("message", {}).get("id")
    channel_id = interaction.get("channel_id")

    if not pending:
        if msg_id:
            alerts.delete_message(channel_id, msg_id)
        return jsonify({"type": 6})

    state.snooze(pending["item_key"], hours=24)
    state.remove_pending(approval_id)
    state.log_action("DECLINE", pending["name"], username)
    alerts.send_declined(channel_id, msg_id, pending["name"], username)
    return jsonify({"type": 6})


def _handle_stock_snooze(variant_id: str, interaction) -> any:
    user_id, username = _user_info(interaction)
    msg_id     = interaction.get("message", {}).get("id")
    channel_id = interaction.get("channel_id")

    state.snooze_stock(variant_id, hours=72)
    log(f"[stock] Snoozed 3 days: {variant_id} by {username}")
    if msg_id:
        alerts.delete_message(channel_id, msg_id)
    return jsonify({"type": 6})


# ── Background loop ────────────────────────────────────────────────────────────

def _run_loop() -> None:
    time.sleep(10)  # Let gunicorn finish starting
    while True:
        try:
            checker.run_check()
            if STORE_ADAPTER == "shopify":
                from updater import get_shopify_products
                products = get_shopify_products()
                checker.run_stock_check(products)
        except Exception as e:
            log(f"[loop] Unhandled error: {e}")
        time.sleep(CHECK_INTERVAL_SECS)


# ── Startup ────────────────────────────────────────────────────────────────────

_started = False
_start_lock = threading.Lock()

def _startup() -> None:
    global _started
    with _start_lock:
        if _started:
            return
        _started = True

    log("=" * 52)
    log("  MM2 PRICE MONITOR")
    log("=" * 52)
    from config import (
        DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID,
        SHOPIFY_STORE, CHECK_INTERVAL_SECS, UNDERCUT_PERCENT,
    )
    log(f"  Bot token:    {'✓' if DISCORD_BOT_TOKEN else '✗ NOT SET'}")
    log(f"  Channel:      {DISCORD_CHANNEL_ID or '✗ NOT SET'}")
    log(f"  Store:        {STORE_ADAPTER.upper()} — {SHOPIFY_STORE or 'custom endpoint'}")
    log(f"  Interval:     {CHECK_INTERVAL_SECS}s")
    log(f"  Undercut:     {UNDERCUT_PERCENT * 100:.1f}%")
    if not API_SECRET:
        log("  ⚠  API_SECRET not set — admin endpoints are open!")
    log("=" * 52)

    threading.Thread(target=_run_loop, daemon=True).start()
    gateway.start()


_startup()


if __name__ == "__main__":
    log(f"Starting on port {PORT}…")
    app.run(host="0.0.0.0", port=PORT)
