"""
updater.py — Store-agnostic price update layer.

Set STORE_ADAPTER in your .env:
  STORE_ADAPTER=shopify   →  updates via Shopify Admin API
  STORE_ADAPTER=custom    →  POSTs to your own endpoint

Adding a new platform is just adding a new adapter function at the bottom
and wiring it in update_price().
"""

import time
import requests
from config import (
    STORE_ADAPTER,
    SHOPIFY_STORE, SHOPIFY_TOKEN,
    CUSTOM_STORE_URL, CUSTOM_STORE_TOKEN,
)
from logger import log


def update_price(variant_id, new_price: float) -> bool:
    """
    Update a single item's price.
    Returns True on success, False on failure.
    """
    if STORE_ADAPTER == "shopify":
        return _shopify_update(variant_id, new_price)
    elif STORE_ADAPTER == "custom":
        return _custom_update(variant_id, new_price)
    else:
        log(f"[updater] Unknown STORE_ADAPTER='{STORE_ADAPTER}'")
        return False


# ── Shopify adapter ────────────────────────────────────────────────────────────

def _shopify_request(method: str, url: str, headers: dict, json=None, retries=2):
    """Shopify API call with automatic 429 retry."""
    for attempt in range(retries + 1):
        try:
            resp = requests.request(method, url, headers=headers, json=json, timeout=30)
            if resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After", "2"))
                log(f"[shopify] Rate limited, retrying in {wait}s…")
                time.sleep(wait)
                continue
            return resp
        except Exception as e:
            if attempt == retries:
                raise
            log(f"[shopify] Request error (attempt {attempt + 1}): {e}")
            time.sleep(1)
    return None


def _shopify_update(variant_id, new_price: float) -> bool:
    if not SHOPIFY_STORE or not SHOPIFY_TOKEN:
        log("[shopify] Missing SHOPIFY_STORE or SHOPIFY_TOKEN")
        return False

    url     = f"https://{SHOPIFY_STORE}/admin/api/2025-01/variants/{variant_id}.json"
    headers = {"X-Shopify-Access-Token": SHOPIFY_TOKEN, "Content-Type": "application/json"}
    payload = {"variant": {"id": variant_id, "price": str(new_price)}}

    try:
        resp = _shopify_request("PUT", url, headers, json=payload)
        success = resp is not None and resp.status_code == 200
        if not success:
            log(f"[shopify] Update failed: {resp.status_code if resp else 'no response'}")
        return success
    except Exception as e:
        log(f"[shopify] Update error: {e}")
        return False


# ── Custom / fullstack adapter ─────────────────────────────────────────────────
#
# Your endpoint should accept:
#   POST  /api/update-price
#   Authorization: Bearer <CUSTOM_STORE_TOKEN>
#   Content-Type: application/json
#   {"variant_id": "...", "new_price": 1.23}
#
# And return 200 on success.

def _custom_update(variant_id, new_price: float) -> bool:
    if not CUSTOM_STORE_URL or not CUSTOM_STORE_TOKEN:
        log("[custom] Missing CUSTOM_STORE_URL or CUSTOM_STORE_TOKEN")
        return False

    headers = {
        "Authorization": f"Bearer {CUSTOM_STORE_TOKEN}",
        "Content-Type":  "application/json",
    }
    payload = {"variant_id": str(variant_id), "new_price": new_price}

    try:
        resp = requests.post(CUSTOM_STORE_URL, headers=headers, json=payload, timeout=15)
        success = resp.status_code == 200
        if not success:
            log(f"[custom] Update failed: {resp.status_code} — {resp.text[:200]}")
        return success
    except Exception as e:
        log(f"[custom] Update error: {e}")
        return False


# ── Shopify product helpers (only used with shopify adapter) ───────────────────

_collection_id_cache = None

def get_shopify_products(collection_keyword="mm2") -> list:
    """
    Fetches all products from the MM2 Shopify collection using cursor pagination.
    Returns [] if not using Shopify adapter or credentials are missing.
    """
    global _collection_id_cache

    if STORE_ADAPTER != "shopify" or not SHOPIFY_STORE or not SHOPIFY_TOKEN:
        return []

    headers = {"X-Shopify-Access-Token": SHOPIFY_TOKEN}

    # Find the collection ID once and cache it
    if _collection_id_cache is None:
        for ctype in ["custom_collections", "smart_collections"]:
            url = f"https://{SHOPIFY_STORE}/admin/api/2025-01/{ctype}.json?limit=250"
            try:
                resp = _shopify_request("GET", url, headers)
                if resp and resp.status_code == 200:
                    for c in resp.json().get(ctype, []):
                        handle = (c.get("handle") or "").lower()
                        title  = (c.get("title")  or "").lower()
                        if collection_keyword in handle or collection_keyword in title:
                            _collection_id_cache = c["id"]
                            log(f"[shopify] Found collection: {c['title']} ({c['id']})")
                            break
            except Exception as e:
                log(f"[shopify] Collection lookup error: {e}")
            if _collection_id_cache:
                break

    # Paginate through products
    all_products = []
    base_url = (
        f"https://{SHOPIFY_STORE}/admin/api/2025-01/products.json"
        f"?collection_id={_collection_id_cache}&limit=250"
        if _collection_id_cache
        else f"https://{SHOPIFY_STORE}/admin/api/2025-01/products.json?limit=250"
    )
    page_info = None

    while True:
        url = (
            f"https://{SHOPIFY_STORE}/admin/api/2025-01/products.json?limit=250&page_info={page_info}"
            if page_info else base_url
        )
        try:
            resp = _shopify_request("GET", url, headers, retries=2)
            if not resp or resp.status_code != 200:
                break
            products = resp.json().get("products", [])
            if not products:
                break
            all_products.extend(products)

            link = resp.headers.get("Link", "")
            if 'rel="next"' in link:
                import re
                m = re.search(r'page_info=([^>]+)>; rel="next"', link)
                page_info = m.group(1) if m else None
                if not page_info:
                    break
            else:
                break
        except Exception as e:
            log(f"[shopify] Products fetch error: {e}")
            break

    log(f"[shopify] Fetched {len(all_products)} products from admin API")
    return all_products


def shopify_update_inventory(variant_id, quantity: int) -> bool:
    """Update inventory quantity for a variant (optional utility)."""
    if not SHOPIFY_STORE or not SHOPIFY_TOKEN:
        return False

    headers = {"X-Shopify-Access-Token": SHOPIFY_TOKEN, "Content-Type": "application/json"}

    # First get the inventory_item_id
    url = f"https://{SHOPIFY_STORE}/admin/api/2025-01/variants/{variant_id}.json"
    try:
        resp = _shopify_request("GET", url, headers)
        if not resp or resp.status_code != 200:
            return False
        inv_item_id = resp.json().get("variant", {}).get("inventory_item_id")
        if not inv_item_id:
            return False

        # Get location ID
        loc_url = f"https://{SHOPIFY_STORE}/admin/api/2025-01/locations.json"
        loc_resp = _shopify_request("GET", loc_url, headers)
        if not loc_resp or loc_resp.status_code != 200:
            return False
        locations = loc_resp.json().get("locations", [])
        if not locations:
            return False
        location_id = locations[0]["id"]

        # Set inventory level
        set_url = f"https://{SHOPIFY_STORE}/admin/api/2025-01/inventory_levels/set.json"
        payload = {"location_id": location_id, "inventory_item_id": inv_item_id, "available": quantity}
        set_resp = _shopify_request("POST", set_url, headers, json=payload)
        return set_resp is not None and set_resp.status_code == 200

    except Exception as e:
        log(f"[shopify] Inventory update error: {e}")
        return False
