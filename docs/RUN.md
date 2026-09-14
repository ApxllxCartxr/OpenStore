# Running OpenStore

This documents how to run **this repo's own demo instance** (two merchants —
Gelateria Milano and Chai House — plus a federated buyer and a merchant bot),
not the generic `openstore init` quickstart for a fresh install (see the
[README](../README.md) for that).

## 1. Install

```bash
uv sync
```

Python 3.12, dependencies resolved via `uv` (see `pyproject.toml`).

## 2. Configure secrets

Two env files at repo root, both gitignored:

- `.env` — `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` / `RAZORPAY_WEBHOOK_SECRET`
  (test-mode), `DISCORD_BOT_TOKEN` (gelateria + chai server bot), `BUYER_DISCORD_BOT_TOKEN`
  (federated buyer process — must be a **different** bot identity than
  `DISCORD_BOT_TOKEN`), `MERCHANT_BOT_TOKEN` (merchant-facing reporting bot —
  also a distinct identity), and per-merchant OAuth credentials for the buyer
  (`GELATERIA_BUYER_CLIENT_ID`/`_SECRET`, `CHAI_BUYER_CLIENT_ID`/`_SECRET` —
  see step 5, these are generated, not chosen).
- `.env.llm` — `LLM_PROVIDER_CHAIN` plus API keys for whichever providers you
  list (`GROQ_API_KEY`, `OPENROUTER_API_KEY`, `NVIDIA_API_KEY`, ...). Loaded
  by `FailoverProvider` — the first working provider in the chain serves every
  agent call (buyer planning, merchant negotiation, campaign drafting).

## 3. Config and data layout

```
configs/
├── gelateria.yaml     # merchant 1 — port 8000
├── chai.yaml           # merchant 2 — port 8001, catalog_path: chai_catalog.yaml
├── chai_catalog.yaml
├── catalog.yaml        # gelateria's catalog (default catalog_path)
└── buyer.yaml           # federated buyer process — talks to both merchants over HTTP

data/                    # SQLite DBs — gitignored, created by migrations on first boot
├── openstore.db         # gelateria
├── chai_openstore.db    # chai
└── buyer.db             # buyer process's own DB (OAuth tokens, cart state)
```

`database.url` in each config is a relative `sqlite:///data/...` path, resolved
against the process's working directory — **always run every command from the
repo root.**

## 4. Start the two merchant servers

```bash
uv run openstore serve configs/gelateria.yaml --port 8000
uv run openstore serve configs/chai.yaml --port 8001
```

Each boot applies pending Alembic migrations automatically, then serves the
`/.well-known/*` manifests, `/agent/mcp`, Policy Studio, Campaign Studio, and
(if `discord.bot_token` is set and `buyer_bot_enabled: false`, as both demo
configs have it) starts a trace-only Discord client that posts to
`#merchant-server-hooks` / `#audit-logs`.

## 5. One-time: register the buyer as an OAuth client on each merchant

```bash
uv run openstore federation-register-buyer configs/gelateria.yaml
uv run openstore federation-register-buyer configs/chai.yaml
```

Paste the printed `client_id`/`client_secret` pairs into `.env` as
`GELATERIA_BUYER_CLIENT_ID`/`_SECRET` and `CHAI_BUYER_CLIENT_ID`/`_SECRET`.
Only needed once per merchant DB — re-running creates a new client, it doesn't
rotate the existing one.

## 6. Seed demo analytics history (optional, needed for campaign drafting)

```bash
uv run python scripts/demo_seed.py gelateria-milano --config configs/gelateria.yaml
uv run python scripts/demo_seed.py chai-house --config configs/chai.yaml
```

Idempotent-ish per run (each call adds a fresh batch of historic `PAID`
checkouts spread over the last 30 days) — re-run only if you've wiped `data/`.
This is what gives the campaign agent's analytics view and the growth-trigger
loop's stall detector something real to read.

## 7. Start the federated buyer

```bash
uv run openstore-buyer configs/buyer.yaml
```

One Discord identity, one process, shops across every merchant listed in
`buyer.yaml`. Must run as its own process — `buyer_bot_enabled: false` in both
merchant configs exists specifically so the merchant servers don't also try to
log in as a buyer bot on the same shared token.

## 8. Start the merchant bot (read-only reporting, any number of merchants)

```bash
uv run openstore merchant-bot configs/gelateria.yaml configs/chai.yaml
```

DM it on Discord: campaign status, exposure remaining against policy caps,
recent orders. It has no mutating action in its action set — it cannot
approve, pause, or touch money, by construction, not by prompt instruction.

## 9. Use it

- **Policy Studio** (`http://localhost:8000/intent/studio`, `:8001` for chai)
  — enrol a WebAuthn passkey, sign an `IntentPolicy` (spend caps, allowed
  tags/SKUs). This is the one-time human authorization step; nothing is
  transactable before it.
- **Discord** — DM the buyer bot ("show me the menu", "get me two vegan
  gelatos", "cancel my order"...). First message with no signed policy for
  that merchant gets a one-time signing link.
- **Campaign Studio** (`/campaign/studio`) — approve or reject
  `PENDING_APPROVAL` campaigns (drafted manually via
  `openstore campaign draft`, or automatically by the growth-trigger loop —
  see below) with a passkey tap.
- **Growth-trigger loop** — runs automatically in the background inside each
  `openstore serve` process (hourly by default, `campaign.growth_check_interval_seconds`).
  To trigger it on demand instead of waiting:
  ```bash
  uv run openstore campaign check-growth configs/chai.yaml
  ```
- **Verify a completed order's evidence, fully offline:**
  ```bash
  curl http://localhost:8000/orders/<checkout_id>/evidence -o bundle.json
  uv run openstore-verify bundle.json --merchant-jwks <(curl -s http://localhost:8000/.well-known/poai-jwks.json)
  ```
  Or open `http://localhost:8000/orders/<checkout_id>/evidence/view` for the
  same 14 checks re-run client-side in the browser (zero-dependency, no server
  trust required).

## Stopping / restarting

All four processes are independent — `kill` any of them and restart with the
same command; migrations are idempotent and the DB survives restarts. To wipe
and start over: delete the three files under `data/`, restart the servers
(migrations recreate schema from scratch), then repeat steps 5–6.

## Shopify-backed pilot (Stage 18)

The same Gelateria merchant, but the catalog comes from a real Shopify dev
store instead of YAML. Run it **instead of** `:8000`, never alongside (same
merchant_id, separate DB):

```bash
uv run openstore serve configs/shopify.yaml --port 8002
```

Needs `SHOPIFY_STORE_DOMAIN` / `SHOPIFY_CLIENT_ID` / `SHOPIFY_CLIENT_SECRET`
in `.env` (one-time `bash /tmp/opencode/shopify-dev-store.sh` wizard). The
store currency must equal the merchant currency (`INR`) — any other currency
fails boot-loud on first catalog read, by design (converting money invents
money). Variants without SKUs are skipped loudly; a store with zero usable
variants refuses to serve a catalog at all.

## Running the test suite

```bash
uv run pytest -q                        # 681 tests
uv run mypy src/
uv run ruff check src/ tests/
uv run python scripts/registry_diff.py  # must print nothing, exit 0
```

No live credentials required — the suite mocks Razorpay/Discord/LLM calls and
uses in-memory/temp SQLite. `scripts/run_demo.py` is the one script that talks
to real Razorpay test-mode endpoints if credentials are present in `.env`.
