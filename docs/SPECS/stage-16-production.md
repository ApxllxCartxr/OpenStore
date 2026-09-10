# Stage 16 — Production hardening (S16)

Closes every recorded-open trust gap and ships the sidecar as a deployable
system. Stages 12–15 shipped against numbered DECISIONs; this spec covers the
full-production build (operator-authorised, money path open).

## Scope

1. **Postgres foundation** — URL-keyed engine cache, QueuePool for PG,
   SQLite PRAGMAs gated by dialect, `lock_policy_row()` row-level INV-11 lock,
   `DATABASE__URL` honoured by `from_yaml` (regression-tested), Alembic
   0001..0010 verified on Postgres 16, `psycopg[binary]` dependency.
2. **Trust closure**
   - Q-033: `HandoffKind.CART` + `cart_payload`, cart studio ceremony,
     `create_cart_handoff` MCP tool, replay protection via cart-hash binding.
   - Q-026: live compiler digest pinned alongside the legacy fixture digest,
     `scripts/pin_compiler_digest.py`, structurally stricter re-execution
     check (full replay documented as future schema work).
   - Q-027: webhook persists `psp_payment_id` best-effort.
   - Q-035: identifier normalisation + widened `registry_diff`.
3. **Real-time reliability (Q-032)** — pre-release PSP reconcile in the hold
   loop (bounded, non-blocking) + pay-message edit plumbing
   (`discord_message_id`, `set_order_message` tool, `try_edit_dm`).
4. **Deploy** — Dockerfile, compose stack, DEPLOY.md runbook. Proven by fresh
   compose to Alembic head with readiness green on both merchants.

## Explicitly remaining (honest, not hidden)

- Q-007 live-constant capture: runs on the operator's laptop (egress), not in
  any sandbox. Production PSP claims stay provisional until that commit lands.
- Full `compile_decision` replay in the offline verifier: needs the signed
  policy snapshot in the bundle (schema change).
- Third-party (non-Discord) agent discovery beyond the UCP manifest: needs MCP
  wire-conformance + dynamic client registration against real platforms.

## DONE WHEN

- `uv run pytest -q` green (627 + 18 new stage-16 tests).
- `uv run mypy src/` clean; `uv run ruff check src/ tests/` clean.
- `uv run python scripts/registry_diff.py` prints nothing, exit 0.
- `uv run python scripts/pin_compiler_digest.py` prints `pinned:`.
- Fresh `docker compose up` brings both merchants to the Alembic head with
  `/health/ready` green (PostgresqlImpl in migration logs).
