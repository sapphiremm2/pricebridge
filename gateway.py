"""
gateway.py — Discord WebSocket gateway.

Keeps the bot showing as online and listens for admin text commands
($approveall, $declineall, $reset, etc.) in the price channel.
"""

import json
import time
import threading
from collections import deque
import websocket
import storage
import state
import alerts
from updater import update_price
from config import DISCORD_BOT_TOKEN, DISCORD_ADMIN_USER_ID, DISCORD_CHANNEL_ID
from logger import log

_GATEWAY_URL      = "wss://gateway.discord.gg/?v=10&encoding=json"
_INTENTS          = 33281  # GUILDS + GUILD_MESSAGES + MESSAGE_CONTENT
_seen_messages    = deque(maxlen=500)


def start() -> None:
    """Start gateway in a background daemon thread."""
    if not DISCORD_BOT_TOKEN:
        log("[gateway] No bot token — skipping")
        return
    t = threading.Thread(target=_loop, daemon=True)
    t.start()


# ── Gateway reconnect loop ─────────────────────────────────────────────────────

def _loop() -> None:
    while True:
        try:
            ws = websocket.WebSocketApp(
                _GATEWAY_URL,
                on_open    = _on_open,
                on_message = _on_message,
                on_error   = lambda ws, e: log(f"[gateway] Error: {e}"),
                on_close   = lambda ws, code, msg: log(f"[gateway] Closed: {code}"),
            )
            ws.run_forever()
        except Exception as e:
            log(f"[gateway] Unexpected error: {e}")
        log("[gateway] Disconnected — reconnecting in 5s…")
        time.sleep(5)


def _on_open(ws) -> None:
    log("[gateway] Connected")


def _on_message(ws, raw: str) -> None:
    data = json.loads(raw)
    op   = data.get("op")
    t    = data.get("t")

    if op == 10:  # HELLO
        interval = data["d"]["heartbeat_interval"]
        log(f"[gateway] Heartbeat: {interval}ms")
        _identify(ws)
        threading.Thread(target=_heartbeat, args=(ws, interval), daemon=True).start()

    elif op == 7:   ws.close()  # RECONNECT
    elif op == 9:   ws.close()  # INVALID SESSION

    elif op == 0 and t == "MESSAGE_CREATE":
        _handle_message(data["d"])


def _identify(ws) -> None:
    ws.send(json.dumps({
        "op": 2,
        "d": {
            "token":      DISCORD_BOT_TOKEN,
            "intents":    _INTENTS,
            "properties": {"os": "linux", "browser": "mm2-monitor", "device": "mm2-monitor"},
            "presence":   {"status": "online", "activities": []},
        },
    }))


def _heartbeat(ws, interval_ms: int) -> None:
    while True:
        time.sleep(interval_ms / 1000)
        try:
            ws.send(json.dumps({"op": 1, "d": None}))
        except Exception:
            break


# ── Text command handler ───────────────────────────────────────────────────────

def _handle_message(msg: dict) -> None:
    msg_id    = msg.get("id", "")
    author_id = msg.get("author", {}).get("id", "")
    channel_id = msg.get("channel_id", "")
    content   = (msg.get("content") or "").strip().lower()

    # Dedupe
    if msg_id in _seen_messages:
        return
    _seen_messages.append(msg_id)

    # Only admin user, only in the price channel
    if author_id != DISCORD_ADMIN_USER_ID:
        return
    if channel_id != DISCORD_CHANNEL_ID:
        return

    username = msg.get("author", {}).get("username", "admin")

    if content == "$approveall":
        threading.Thread(
            target=_approve_all, args=(channel_id, username), daemon=True
        ).start()

    elif content == "$declineall":
        threading.Thread(
            target=_decline_all, args=(channel_id, username), daemon=True
        ).start()

    elif content == "$reset":
        state.clear_pending()
        storage.save("prices.json", {})
        state.clear_all_tracking()
        alerts.send_confirmation(channel_id, "Reset", "Prices, pending, and tracking cleared.")

    elif content == "$resetstock":
        state.clear_snoozed_stock()
        alerts.send_confirmation(channel_id, "Stock Reset", "Stock snoozes cleared — fresh alerts on next check.")

    elif content == "$resettracking":
        state.clear_all_tracking()
        alerts.send_confirmation(channel_id, "Tracking Reset", "Price stability tracking cleared.")

    elif content == "$help":
        alerts.send_help(channel_id)


def _approve_all(channel_id: str, username: str) -> None:
    pending = state.all_pending()
    approved = 0
    for aid, data in list(pending.items()):
        if data.get("channel_id") != channel_id:
            continue
        if update_price(data["variant_id"], data["new_price"]):
            state.log_action("APPROVE", data["name"], username, data["old_price"], data["new_price"])
            alerts.send_approved(channel_id, data.get("message_id"), data["name"],
                                  data["old_price"], data["new_price"], username)
            state.remove_pending(aid)
            approved += 1
            time.sleep(0.4)
    log(f"[gateway] $approveall: {approved} items by {username}")


def _decline_all(channel_id: str, username: str) -> None:
    pending = state.all_pending()
    declined = 0
    for aid, data in list(pending.items()):
        if data.get("channel_id") != channel_id:
            continue
        state.snooze(data.get("item_key", ""), hours=24)
        state.log_action("DECLINE", data["name"], username)
        alerts.send_declined(channel_id, data.get("message_id"), data["name"], username)
        state.remove_pending(aid)
        declined += 1
        time.sleep(0.4)
    log(f"[gateway] $declineall: {declined} items by {username}")
