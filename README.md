# MM2 Price Monitor

Monitors competitors's store prices and sends Discord approval requests when your store needs a price update. Approve in one click — the price updates automatically.

Works with **Shopify** stores or any **custom/fullstack** backend.

---

## How it works

1. Every 10 minutes, fetches all godly/ancient/vintage/legendary/chroma item prices from StarPets
2. Compares against your store's prices
3. If a price has been out of sync for 15 minutes (stability buffer prevents noise), sends a Discord embed with **Approve** / **Decline** buttons
4. **Approve** → price is updated immediately via your store's API
5. **Decline** → item is snoozed for 24 hours

---

## Deploy to Railway (fastest)

1. Fork / push this repo to GitHub
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Add environment variables (see `.env.example`)
4. Copy the Railway public URL (e.g. `https://mm2-monitor.up.railway.app`)
5. In Discord Developer Portal → your app → Interactions Endpoint URL → paste `<your-url>/interactions`

Optional but recommended: add [Upstash Redis](https://upstash.com) for persistent state across redeploys.

---

## Environment variables

See `.env.example` — every variable is documented there.

---

## Store adapters

### Shopify
Set `STORE_ADAPTER=shopify` and provide `SHOPIFY_STORE` + `SHOPIFY_TOKEN`.

**Getting a Shopify token:**
1. Shopify Admin → Settings → Apps → Develop apps
2. Create app → Configure Admin API scopes: `read_products`, `write_products`, `read_inventory`
3. Install → copy the Admin API access token

### Custom / fullstack
Set `STORE_ADAPTER=custom` and provide:
- `CUSTOM_STORE_URL` — your price update endpoint (e.g. `https://yoursite.com/api/update-price`)
- `CUSTOM_STORE_TOKEN` — a secret Bearer token your endpoint validates

Your endpoint receives:
```json
POST /api/update-price
Authorization: Bearer <CUSTOM_STORE_TOKEN>

{ "variant_id": "abc123", "new_price": 1.49 }
```
Return `200` on success.

You also need a **catalogue endpoint** at `<base>/catalogue` that returns your current prices in this shape:
```json
{
  "godly knife|regular": {
    "name": "Godly Knife",
    "price": 1.99,
    "variant_id": "abc123",
    "product_id": "xyz789",
    "image": "https://...",
    "is_chroma": false
  }
}
```
The key format is `{item_name_lowercase}|{chroma|regular}`.

---

## Discord commands

Send these in the price alerts channel (admin user only):

| Command | Action |
|---|---|
| `$approveall` | Approve all pending price changes |
| `$declineall` | Decline + snooze all pending (24h) |
| `$reset` | Clear all pending + saved prices + tracking |
| `$resetstock` | Clear stock snoozes |
| `$resettracking` | Clear price stability tracking only |
| `$help` | Show this list |

---

## HTTP endpoints

All require `?key=<API_SECRET>` unless `API_SECRET` is unset.

| Endpoint | Action |
|---|---|
| `GET /` | Health check |
| `GET /reset` | Same as `$reset` |
| `GET /resetstock` | Same as `$resetstock` |
| `GET /resettracking` | Same as `$resettracking` |

---

## File structure

```
main.py        Flask app + Discord interaction handler + startup
checker.py     Core price comparison + stock check
fetchers.py    StarPets + Shopify storefront price fetchers
updater.py     Store-agnostic price update (Shopify / custom adapters)
alerts.py      Discord embed builders + send helpers
gateway.py     Discord WebSocket gateway (online status + text commands)
state.py       Snooze, pending approvals, price stability tracking
storage.py     Redis / local JSON persistence layer
config.py      Environment variable loading
logger.py      Timestamped console logger
```

---

## Adding a new store platform

1. Open `updater.py`
2. Add a new `_yourplatform_update(variant_id, new_price)` function
3. Wire it into `update_price()` with `elif STORE_ADAPTER == "yourplatform"`
4. Add the corresponding env var to `.env.example`
5. Open `fetchers.py` and add a matching `get_store_prices()` implementation (or reuse the custom catalogue pattern from `checker.py`)
