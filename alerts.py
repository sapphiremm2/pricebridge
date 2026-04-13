"""
alerts.py — Discord embed builders and message senders.

All Discord API calls live here. Nothing else should import requests for Discord.
"""

import requests
from config import (
    DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID,
    DISCORD_STOCK_CHANNEL_ID, DISCORD_PRICE_ROLE_ID, DISCORD_STOCK_ROLE_ID,
)
from logger import log


# ── Low-level send ─────────────────────────────────────────────────────────────

def _send(channel_id: str, payload: dict) -> str | None:
    """Send a message to a channel. Returns message ID on success, None on failure."""
    if not DISCORD_BOT_TOKEN or not channel_id:
        return None
    url     = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}", "Content-Type": "application/json"}
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        if resp.status_code in (200, 201):
            return resp.json().get("id")
        log(f"[discord] Send failed {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log(f"[discord] Send error: {e}")
    return None


def _delete(channel_id: str, message_id: str) -> None:
    if not DISCORD_BOT_TOKEN or not channel_id or not message_id:
        return
    url     = f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}"
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
    try:
        requests.delete(url, headers=headers, timeout=10)
    except Exception as e:
        log(f"[discord] Delete error: {e}")


# ── Price alert ────────────────────────────────────────────────────────────────

def send_price_alert(sp_data: dict, store_data: dict, sp_price: float,
                     new_price: float, approval_id: str, direction: str) -> str | None:
    """
    direction: "lower"  → SP is cheaper, suggest lowering store price (red)
               "higher" → SP is more expensive, can raise store price (green)
    """
    color  = 0xED4245 if direction == "lower" else 0x57F287
    prefix = "Lower Price" if direction == "lower" else "Raise Price"

    store_url = f"https://buyblox.gg/products/{store_data['name'].lower().replace(' ', '-').replace(\"'\", '')}"
    sp_url    = sp_data.get("url", "https://starpets.gg/mm2")

    embed = {
        "title": f"{prefix}: {store_data['name']}",
        "color": color,
        "fields": [
            {"name": "Your Store",  "value": f"${store_data['price']:.2f}", "inline": True},
            {"name": "StarPets",    "value": f"${sp_price:.2f}",            "inline": True},
            {"name": "New Price",   "value": f"${new_price:.2f}",           "inline": True},
            {"name": "Links",
             "value": f"[Your Store]({store_url}) · [StarPets]({sp_url})",
             "inline": False},
        ],
    }
    if store_data.get("image"):
        embed["thumbnail"] = {"url": store_data["image"]}

    components = [{"type": 1, "components": [
        {"type": 2, "style": 3, "label": "Approve", "custom_id": f"approve_{approval_id}"},
        {"type": 2, "style": 4, "label": "Decline", "custom_id": f"decline_{approval_id}"},
    ]}]

    content = f"<@&{DISCORD_PRICE_ROLE_ID}>" if DISCORD_PRICE_ROLE_ID else ""
    payload = {"content": content, "embeds": [embed], "components": components}

    return _send(DISCORD_CHANNEL_ID, payload)


# ── Approval result (replaces the alert embed) ────────────────────────────────

def send_approved(channel_id: str, msg_id: str, name: str,
                  old_price: float, new_price: float, username: str) -> None:
    _delete(channel_id, msg_id)
    _send(channel_id, {"embeds": [{
        "title": f"✅ Updated: {name}",
        "color": 0x57F287,
        "fields": [
            {"name": "Old", "value": f"${old_price:.2f}", "inline": True},
            {"name": "New", "value": f"${new_price:.2f}", "inline": True},
        ],
        "footer": {"text": f"Approved by {username}"},
    }]})


def send_declined(channel_id: str, msg_id: str, name: str, username: str) -> None:
    _delete(channel_id, msg_id)
    _send(channel_id, {"embeds": [{
        "title": f"❌ Declined: {name}",
        "color": 0xED4245,
        "description": "Snoozed 24 hours.",
        "footer": {"text": f"Declined by {username}"},
    }]})


# ── Stock alert ────────────────────────────────────────────────────────────────

def send_stock_alert(name: str, variant_id) -> None:
    channel  = DISCORD_STOCK_CHANNEL_ID or DISCORD_CHANNEL_ID
    slug     = name.lower().replace(" ", "-").replace("'", "")
    store_url = f"https://buyblox.gg/products/{slug}"
    snooze_id = f"stock_snooze_{variant_id}"

    embed = {
        "title":       f"⚠️ Out of Stock: {name}",
        "color":       0xFEE75C,
        "description": f"[View on store]({store_url})",
    }
    components = [{"type": 1, "components": [
        {"type": 2, "style": 2, "label": "Snooze 3 days", "custom_id": snooze_id},
    ]}]
    content = f"<@&{DISCORD_STOCK_ROLE_ID}>" if DISCORD_STOCK_ROLE_ID else ""
    _send(channel, {"content": content, "embeds": [embed], "components": components})


# ── Admin command confirmations ────────────────────────────────────────────────

def send_confirmation(channel_id: str, title: str, description: str) -> None:
    _send(channel_id, {"embeds": [{"title": title, "color": 0x5865F2, "description": description}]})


def send_help(channel_id: str) -> None:
    _send(channel_id, {"embeds": [{
        "title": "Commands",
        "color": 0x5865F2,
        "description": (
            "**$approveall** — Approve all pending in this channel\n"
            "**$declineall** — Decline all pending in this channel\n"
            "**$reset** — Clear pending + saved prices + stability tracking\n"
            "**$resetstock** — Clear stock snoozes (re-alerts on next check)\n"
            "**$resettracking** — Clear price stability tracking only\n"
            "**$help** — Show this message"
        ),
    }]})


# ── Delete helper (public) ─────────────────────────────────────────────────────

def delete_message(channel_id: str, message_id: str) -> None:
    _delete(channel_id, message_id)
