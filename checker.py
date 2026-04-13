"""
checker.py — Core price comparison loop.

Called on a schedule from main.py. Compares StarPets vs your store,
applies stability filtering, and queues Discord approval requests.
"""

import time
import storage
import state
import alerts
from fetchers import get_starpets_prices, get_store_prices
from config import (
    SHOPIFY_STORE, STORE_ADAPTER, CUSTOM_STORE_URL,
    UNDERCUT_PERCENT, RAISE_THRESHOLD_PERCENT,
    STABILITY_MINUTES, STABILITY_BUFFER,
    DISCORD_CHANNEL_ID,
)
from logger import log

_PRICE_FILE = "prices.json"

# How large a price gap (% + abs) triggers a "likely wrong match" skip
_MISMATCH_PERCENT = 0.70
_MISMATCH_ABS     = 1.00


def run_check() -> None:
    """
    Main price check. Fetches both sources, compares, and sends Discord
    approval requests for items that have been out-of-sync long enough.
    """
    log("── Price check starting ──")

    saved_prices = storage.load(_PRICE_FILE)
    sp_prices    = get_starpets_prices()
    store_prices = _get_store_prices()

    if not sp_prices:
        log("[checker] StarPets returned nothing — skipping (API may be down)")
        return
    if not store_prices:
        log("[checker] Store returned nothing — skipping")
        return

    # First ever run — save baseline, no alerts yet
    if not saved_prices:
        log("[checker] First run — saving baseline prices, no alerts sent")
        storage.save(_PRICE_FILE, sp_prices)
        return

    state.cleanup_expired()

    already_pending = state.pending_item_keys()
    snoozed         = storage.load("snoozed.json")
    tracking        = storage.load("price_tracking.json")
    now_dt          = __import__("datetime").datetime.now()
    tracking_changed = False
    alerts_sent      = 0
    tracking_count   = 0

    for key, sp_data in sp_prices.items():
        sp_price    = sp_data["price"]
        store_data  = store_prices.get(key)

        if not store_data:
            continue
        if key in already_pending:
            continue
        if _is_snoozed_inline(key, snoozed, now_dt):
            continue

        store_price = store_data["price"]

        if store_price <= 0 or sp_price <= 0:
            continue

        direction = _get_direction(sp_price, store_price)
        if not direction:
            # Prices are close — clear any stale tracking for this key
            if key in tracking:
                del tracking[key]
                tracking_changed = True
            continue

        # Skip implausible matches (giant price gap on a cheap item)
        if _likely_mismatch(sp_price, store_price, store_price):
            log(f"[checker] Skipping likely mismatch: {store_data['name']}")
            continue

        # Price stability check (inline, no extra Redis calls)
        stable, tracking, tracking_changed = _stable_inline(
            key, sp_price, store_price, tracking, now_dt,
            STABILITY_MINUTES, STABILITY_BUFFER, tracking_changed
        )
        if not stable:
            tracking_count += 1
            continue

        # Stable long enough → send alert
        new_price   = round(sp_price * (1 - UNDERCUT_PERCENT), 2)
        approval_id = state.new_approval_id()

        msg_id = alerts.send_price_alert(sp_data, store_data, sp_price, new_price, approval_id, direction)

        if msg_id:
            state.add_pending(approval_id, {
                "item_key":   key,
                "name":       store_data["name"],
                "variant_id": store_data["variant_id"],
                "old_price":  store_price,
                "new_price":  new_price,
                "sp_price":   sp_price,
                "is_chroma":  sp_data.get("is_chroma", False),
                "channel_id": DISCORD_CHANNEL_ID,
                "message_id": msg_id,
            })
            # Clear tracking now that notification is sent
            if key in tracking:
                del tracking[key]
                tracking_changed = True
            alerts_sent += 1
        else:
            log(f"[checker] Discord send failed for {store_data['name']} — will retry")

        time.sleep(0.8)  # gentle rate-limit

    if tracking_changed:
        storage.save("price_tracking.json", tracking)

    if tracking_count:
        log(f"[checker] Tracking {tracking_count} items for {STABILITY_MINUTES}min stability")
    log(f"[checker] Done — {alerts_sent} approval(s) sent")
    storage.save(_PRICE_FILE, sp_prices)


def run_stock_check(products: list = None) -> None:
    """
    Check Shopify inventory. Sends a Discord alert per out-of-stock item,
    then auto-snoozes it for 24 hours so it doesn't spam.
    `products` can be pre-fetched to avoid duplicate Shopify API calls.
    """
    from updater import get_shopify_products
    if STORE_ADAPTER != "shopify":
        return  # Stock check only supported for Shopify right now

    log("── Stock check starting ──")
    if products is None:
        products = get_shopify_products()
    if not products:
        return

    out_of_stock = []
    for product in products:
        for variant in product.get("variants", []):
            if variant.get("inventory_quantity", 0) <= 0:
                vid = variant["id"]
                if not state.is_stock_snoozed(vid):
                    image = (product.get("images") or [{}])[0].get("src", "")
                    out_of_stock.append({"name": product["title"], "variant_id": vid, "image": image})

    for item in out_of_stock:
        alerts.send_stock_alert(item["name"], item["variant_id"])
        state.snooze_stock(item["variant_id"], hours=24)
        time.sleep(0.5)

    log(f"[stock] {len(out_of_stock)} out-of-stock alert(s) sent")


# ── Internal helpers ───────────────────────────────────────────────────────────

def _get_store_prices() -> dict:
    """Get store prices from the appropriate source."""
    if STORE_ADAPTER == "shopify" and SHOPIFY_STORE:
        return get_store_prices(SHOPIFY_STORE)
    elif STORE_ADAPTER == "custom" and CUSTOM_STORE_URL:
        # For custom adapter, the store prices endpoint is different.
        # Fetch from a catalogue endpoint: CUSTOM_STORE_URL with /catalogue appended,
        # expecting same shape as get_store_prices() return value.
        import requests
        from config import CUSTOM_STORE_TOKEN
        try:
            base = CUSTOM_STORE_URL.rstrip("/").rsplit("/", 1)[0]  # strip /update-price
            resp = requests.get(
                f"{base}/catalogue",
                headers={"Authorization": f"Bearer {CUSTOM_STORE_TOKEN}"},
                timeout=30,
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            log(f"[checker] Custom catalogue fetch error: {e}")
        return {}
    return {}


def _get_direction(sp_price: float, store_price: float) -> str | None:
    if sp_price < store_price - 0.01:
        return "lower"
    if sp_price > store_price * (1 + RAISE_THRESHOLD_PERCENT):
        return "higher"
    return None


def _likely_mismatch(sp_price: float, store_price: float, reference: float) -> bool:
    if reference < 0.50:
        return False
    pct = abs(sp_price - store_price) / reference
    diff = abs(sp_price - store_price)
    return pct > _MISMATCH_PERCENT and diff > _MISMATCH_ABS


def _is_snoozed_inline(key: str, snoozed: dict, now) -> bool:
    if key in snoozed:
        from datetime import datetime
        until = datetime.fromisoformat(snoozed[key])
        return now < until
    return False


def _stable_inline(key, sp_price, store_price, tracking, now,
                   stability_minutes, buffer, tracking_changed):
    """
    Inline stability check that mutates and returns the tracking dict
    (avoids extra storage reads per item).
    """
    from datetime import datetime

    if key in tracking:
        entry = tracking[key]
        sp_ok = abs(sp_price    - entry["sp_price"])    <= buffer
        st_ok = abs(store_price - entry["store_price"]) <= buffer

        if sp_ok and st_ok:
            elapsed = (now - datetime.fromisoformat(entry["first_seen"])).total_seconds() / 60
            if elapsed >= stability_minutes:
                return True, tracking, tracking_changed
            return False, tracking, tracking_changed

        # Price drifted — reset
        tracking[key] = {"sp_price": sp_price, "store_price": store_price, "first_seen": now.isoformat()}
        return False, tracking, True

    tracking[key] = {"sp_price": sp_price, "store_price": store_price, "first_seen": now.isoformat()}
    return False, tracking, True
