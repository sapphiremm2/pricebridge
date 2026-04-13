"""
fetchers.py — Pulls current prices from StarPets and your store.

StarPets:  hits their public market API, normalises into a flat dict keyed by
           "{item_name_lower}|{chroma|regular}".

Store:     Shopify storefront JSON endpoint (no auth required, public).
           If you're on a custom stack, swap get_store_prices() for a call
           to your own catalogue endpoint — just return the same shape.
"""

import re
import time
import requests
from logger import log


# ── StarPets ──────────────────────────────────────────────────────────────────

_SP_API    = "https://mm2-market.apineural.com/api/store/items/all"
_SP_HEADERS = {
    "content-type": "application/json",
    "origin":       "https://starpets.gg",
    "referer":      "https://starpets.gg/",
}
_TRACKED_RARITIES = {"godly", "ancient", "vintage", "legendary", "chroma"}


def get_starpets_prices() -> dict:
    """
    Returns dict keyed by item_key → {name, price, rarity, is_chroma, url}
    Only tracks godly / ancient / vintage / legendary / chroma.
    """
    items = {}
    page  = 1

    while page <= 50:
        payload = {
            "filter":   {"types": [{"type": "weapon"}, {"type": "pet"}, {"type": "misc"}]},
            "page":     page,
            "amount":   72,
            "currency": "usd",
            "sort":     {"popularity": "desc"},
        }
        try:
            resp = requests.post(_SP_API, headers=_SP_HEADERS, json=payload, timeout=60)
            resp.raise_for_status()
            data = resp.json().get("items", [])
        except Exception as e:
            log(f"[starpets] Page {page} error: {e}")
            break

        if not data:
            break

        for item in data:
            name   = (item.get("name") or "").strip()
            price  = item.get("price")
            rarity = item.get("rare", "")
            is_chroma  = item.get("chroma") is True or rarity == "chroma"
            item_type  = item.get("type", "weapon")
            item_id    = item.get("id", "")

            if price is None or rarity not in _TRACKED_RARITIES:
                continue

            key = _item_key(name, is_chroma)
            if key not in items or float(price) < items[key]["price"]:
                slug = name.lower().replace(" ", "-").replace("'", "")
                items[key] = {
                    "name":      name,
                    "price":     float(price),
                    "rarity":    rarity,
                    "is_chroma": is_chroma,
                    "url":       f"https://starpets.gg/mm2/shop/{item_type}/{slug}/{item_id}",
                }

        if len(data) < 72:
            break
        page += 1
        time.sleep(0.3)

    log(f"[starpets] Fetched {len(items)} items")
    return items


# ── Your store (Shopify storefront) ───────────────────────────────────────────

def get_store_prices(store_domain: str) -> dict:
    """
    Pulls prices from a Shopify store's public products.json endpoint.
    Returns dict keyed by item_key → {name, price, variant_id, product_id, image, is_chroma}

    For a custom/fullstack store: replace this function body with a call to
    your own catalogue endpoint and return the same dict shape.
    """
    items  = {}
    page   = 1

    while True:
        url = f"https://{store_domain}/collections/mm2/products.json?page={page}&limit=250"
        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
            products = resp.json().get("products", [])
        except Exception as e:
            log(f"[store] Page {page} error: {e}")
            break

        if not products:
            break

        for p in products:
            if not p.get("variants"):
                continue

            title      = p["title"].strip()
            variant    = p["variants"][0]
            price      = float(variant["price"])
            variant_id = variant["id"]
            product_id = p["id"]
            image      = (p.get("images") or [{}])[0].get("src", "")
            is_chroma  = "chroma" in title.lower()
            base_name  = re.sub(r"\bchroma\b\s*", "", title, flags=re.I).strip() if is_chroma else title

            key = _item_key(base_name, is_chroma)
            items[key] = {
                "name":       title,
                "price":      price,
                "variant_id": variant_id,
                "product_id": product_id,
                "image":      image,
                "is_chroma":  is_chroma,
            }

        if len(products) < 250:
            break
        page += 1
        time.sleep(0.3)

    log(f"[store] Fetched {len(items)} items")
    return items


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _item_key(name: str, is_chroma: bool) -> str:
    """Normalised match key used by both fetchers."""
    return f"{name.strip().lower()}|{'chroma' if is_chroma else 'regular'}"
