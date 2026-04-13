"""
config.py — All environment variable loading and derived settings.
Edit .env.example to see what each variable does.
"""

import os

# ── Discord ────────────────────────────────────────────────────────────────────
DISCORD_BOT_TOKEN       = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_PUBLIC_KEY      = os.getenv("DISCORD_PUBLIC_KEY")
DISCORD_CHANNEL_ID      = os.getenv("DISCORD_CHANNEL_ID")       # Price alerts
DISCORD_STOCK_CHANNEL_ID = os.getenv("DISCORD_STOCK_CHANNEL_ID") # Stock alerts (falls back to main)
DISCORD_PRICE_ROLE_ID   = os.getenv("DISCORD_PRICE_ROLE_ID", "")
DISCORD_STOCK_ROLE_ID   = os.getenv("DISCORD_STOCK_ROLE_ID", "")
DISCORD_ALLOWED_ROLES   = [r.strip() for r in os.getenv("DISCORD_ALLOWED_ROLES", "").split(",") if r.strip()]
DISCORD_ADMIN_USER_ID   = os.getenv("DISCORD_ADMIN_USER_ID", "")

# ── Store backend (choose one) ─────────────────────────────────────────────────
#   STORE_ADAPTER = "shopify"  →  uses Shopify Admin API
#   STORE_ADAPTER = "custom"   →  POSTs to your own API endpoint
STORE_ADAPTER = os.getenv("STORE_ADAPTER", "shopify").lower()

# Shopify (only needed when STORE_ADAPTER=shopify)
SHOPIFY_STORE = os.getenv("SHOPIFY_STORE")   # e.g. yourstore.myshopify.com
SHOPIFY_TOKEN = os.getenv("SHOPIFY_TOKEN")   # Admin API access token

# Custom / fullstack (only needed when STORE_ADAPTER=custom)
# Your endpoint receives:  POST  {"variant_id": "...", "new_price": 1.23}
# with header:             Authorization: Bearer <CUSTOM_STORE_TOKEN>
CUSTOM_STORE_URL   = os.getenv("CUSTOM_STORE_URL")    # e.g. https://yoursite.com/api/update-price
CUSTOM_STORE_TOKEN = os.getenv("CUSTOM_STORE_TOKEN")  # Bearer token your API expects

# ── Persistence ───────────────────────────────────────────────────────────────
#   Leave UPSTASH_* blank to fall back to local JSON files (fine for single-instance)
UPSTASH_REST_URL   = os.getenv("UPSTASH_REDIS_REST_URL")
UPSTASH_REST_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")

# ── Pricing behaviour ─────────────────────────────────────────────────────────
CHECK_INTERVAL_SECS     = int(os.getenv("CHECK_INTERVAL", "600"))   # How often to poll (default 10 min)
UNDERCUT_PERCENT        = float(os.getenv("UNDERCUT_PERCENT", "0.01"))  # Undercut by 1%
RAISE_THRESHOLD_PERCENT = float(os.getenv("RAISE_THRESHOLD", "0.20"))   # Alert to raise if SP is 20% higher
STABILITY_MINUTES       = int(os.getenv("PRICE_STABILITY_MINUTES", "15"))
STABILITY_BUFFER        = float(os.getenv("PRICE_STABILITY_BUFFER", "0.03"))

# ── Security ──────────────────────────────────────────────────────────────────
API_SECRET = os.getenv("API_SECRET")   # Protects /reset and other admin HTTP endpoints
PORT       = int(os.getenv("PORT", "3000"))
