# OpenStore production deploy runbook

Single-tenant per sidecar process (DECISION-015): one merchant per `openstore
serve` process, each with its own database. The compose stack runs two demo
merchants, one Postgres server, and the two agent processes.

## 0. Prerequisites

- `POSTGRES_PASSWORD`, `RAZORPAY_KEY_ID` (`rzp_test_*` until the live-key
  ceremony below), `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`,
  `DISCORD_BOT_TOKEN` in the shell or a `.env` file beside the compose file.
- Public HTTPS origin(s) with DNS. Set per merchant in YAML:
  `public_base_url: https://store.example.com` **and**
  `webauthn.rp_id: store.example.com` + `webauthn.origin: https://store.example.com`.
  Mismatch fails boot loud (SID-5).
- Razorpay dashboard: webhook URL `https://<host>/webhooks/razorpay` with the
  webhook secret; test-mode keys only.

## 1. First boot

```bash
docker compose up --build -d db
docker compose up --build -d gelateria chai
docker compose logs gelateria | grep -i "migrations\|ready"
curl -s localhost:8000/health/ready
curl -s localhost:8001/health/ready
```

Migrations run at boot (`apply_migrations`, SID-3). Readiness gates
agent/money traffic with 503 until config + schema + DB + test keys + signing
keys all pass (`/health/ready`, SID-2). Agents come up under the `agents`
profile:

```bash
docker compose --profile agents up -d buyer merchant-bot
```

## 2. Razorpay live-constant capture (Q-007, one time per PSP change)

Sandbox has no `api.razorpay.com` egress, so this runs on your laptop (any
machine with normal internet — that is all "egress machine" means):

```bash
export RAZORPAY_KEY_ID=rzp_test_... RAZORPAY_KEY_SECRET=... RAZORPAY_WEBHOOK_SECRET=...
uv run python scripts/capture_constants.py
uv run pytest tests/stage05 -q
git commit -am "chore(razorpay): pin verify-at-build constants <ISO-date>"
```

Production stays blocked on unverified constants until this commit lands.

## 3. Operating

- Metrics: `GET /internal/metrics` (Prometheus exposition: readiness, hold
  counts by state, ledger balances, reconciliation drift).
- Logs: JSON via `python-json-logger`; every money log carries
  trace_id/client_id. Alert on `sign_count_regression`,
  `reconciliation_drift_total != 0`, DLQ growth, `/health/ready` 503.
- Sweeps: hold-release (30s), campaign-expiry (60s), growth (hourly) run
  in-process; the INV-7 `reconciliation_sweep` covers 10m–7d rows, and the
  hold loop pre-reconciles near-expiry HELD rows from the PSP (Q-032b) so a
  lost webhook cannot kill a paid order.
- Backups: `pg_dump` nightly per merchant DB; evidence bundles
  (`checkouts.poai_bundle`) retained `evidence_retention_days` (default 540).
  Test restore monthly — an untested backup is not a backup.
- Key rotation: PoAI/catalog keys under the merchant key dir; JWKS served at
  `/.well-known/poai-jwks.json`. Rotate by adding the new key, overlapping
  one bundle window, then removing the old and re-pinning verifier digests
  via `scripts/pin_compiler_digest.py --check` in CI.

## 4. Live keys (explicit ceremony, never by accident)

Readiness **fails** on `rzp_live_*` keys (`_razorpay_test_keys`, SID-2).
Going live requires: Q-007 capture redone against live-mode, sustained
live-mode traffic test, on-call rotation, and a code change lifting the
test-mode gate — not an env-var flip.

## 5. Rollback

Images are pinned by git SHA. `docker compose up -d --build` the prior SHA;
migrations are forward-compatible (nullable add-column / enum-add only —
downgrades drop columns, never rewrite data). SID-6.
