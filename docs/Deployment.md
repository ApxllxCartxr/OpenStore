# Deploying a sidecar

> Formerly `docs/DEPLOY.md`; renamed during the documentation reorganisation.

One deploy serves one Merchant domain (ADR-0007).
A real install needs more than `make up`.
`make up` is the demo and creates its own schema.

## Before anything

Two facts decide most of what follows.

1. The signing keys are the identity of the shop. Agents pin the keys. The shop seals receipts with the keys. The verifier checks receipts against the JWKS that publishes the keys. If you lose the keys, every receipt from this shop becomes unverifiable. No other copy exists, so no recovery exists.
2. The database of the sidecar belongs to the sidecar alone. It holds the Ledger, the Transcripts, the evidence, and the in-flight money path. It is never the database of the Merchant. The sidecar reads Merchant truth through the nine doors and nowhere else (SPEC §5, ADR-0001).

## 1. Schema

The sidecar refuses to start against a database without a migration.

```
SIDECAR_DATABASE_URL=postgresql+psycopg://... alembic upgrade head
```

Demo creates its own schema. A real deploy does not. If start changes schema as a side effect, rollback can leave a table that old code cannot read. No review approved that change.

So a non-demo boot checks the revision. If the revision is old, the boot stops. The boot names the command to run. The error is `SchemaNotMigrated` and names both revisions.

You run the migration first. Then you start the process. If the migration adds a column, you can run it before the old process stops. If the migration drops or renames a column, do not run it before the old process stops. Split that migration across two releases.

Do not run `alembic downgrade` on a deploy that took an order. `alembic downgrade` exists because alembic wants a pair. The Ledger is append-only. That command can delete the Ledger.

## 2. Secrets

Every secret is per deploy. None of the secrets has a safe default. `.env.example` names all secrets with no values. A template that ships a working secret puts that secret in production.

| Variable | What it is | Losing it |
|---|---|---|
| `SIDECAR_SIGNING_KEY_PATH` | the keyfile | every receipt becomes unverifiable |
| `SIDECAR_SIGNING_KEY_PASSPHRASE` | encrypts that keyfile at rest | the keyfile will not open |
| `DEPLOY_PSEUDONYM_KEY` | derives `consumer_id` | old orders stop correlating to new ones (which is also how you rotate deliberately) |
| `TRAIT_HMAC_SECRET` | signs the nine doors | every door refuses |
| `PROVIDER_WEBHOOK_SECRET` / `RAZORPAY_WEBHOOK_SECRET` | verifies the money callback | every callback is refused, and payments only finish through a manual status check |
| `RAZORPAY_KEY_ID` / `_SECRET` | the Razorpay account of the Merchant | no link can be created, so no prepaid order can be paid |
| `OAUTH_CLIENT_ID` / `_SECRET` | the allowlisted-agent route | that route refuses everyone. Self-registration still works |

You use two webhook names, not one shared value. Razorpay issues the Razorpay secret in the Razorpay dashboard. Do not paste that secret into a generic variable. That variable holds a secret that the Provider did not agree to.

No unauthenticated callback path exists. If no webhook secret is set, every callback is refused. This rule covers demo mode.

You keep secrets in the store of the platform, not in a `.env` file on a shared host. You use one of these stores:

- Fly secrets
- Kubernetes secrets
- a systemd credential

The sidecar reads secrets as environment variables. The sidecar never writes secrets anywhere.

## 3. Back up the keys, and prove the backup

You set `SIDECAR_KEY_EXPORT_PATH` before first boot. The first boot enrolls the key of the shop. That boot writes the export once. That moment is the only moment when a key exists without a backup.

The boot does not rewrite the export afterwards. If the export followed each rotation, it overwrites a copy that the operator did not move yet.

Then you run the check that operators skip:

```
openstore-keys check /backups/keys.json --against /var/lib/openstore/keys.json
```

The command reports two facts. It reports if the backup opens. It reports if the backup holds the key that the shop publishes now.

If a backup opens but misses a key from a rotation, receipts sealed since then verify against nothing. That failure is the failure that this drill finds. The backup looks fine until the day it fails.

If you need a fresh encrypted copy, you run:

```
openstore-keys export /var/lib/openstore/keys.json /backups/keys.json \
    --export-passphrase "$BACKUP_PASSPHRASE"
```

The tool refuses an unencrypted export. The live keyfile can stay unencrypted. Only the operator can reach that host. A copy that travels to a backup lacks that protection.

## 4. Back up the database

The Ledger and the Transcripts are the record of the shop. They record what the shop decided and what money moved. You resolve receipts from that record by id. Ordinary Postgres backups work here.

You back up the keyfile and the database together. A database without its keys holds receipts that nobody can verify. Keys without the database hold an identity with nothing to prove.

## 5. The payment rail

`PAYMENT_PROVIDER=razorpay` talks to the Razorpay account of the Merchant over the Payment Links API. You learn two facts before the first deploy:

- The adapter refuses to boot on a live key while `OPENSTORE_DEMO_MODE` is on. Every demo receipt carries a demo mark. A demo never moves real money.
- The link lives a bit longer than the fifteen-minute window in §16.7. Razorpay refuses an `expire_by` that is not more than fifteen minutes out. The sidecar uses the lifetime of the Provider as the ceiling on the stock hold. So the longer link shortens nothing.

Settlement runs from the Consumer to the account of the Merchant. The sidecar never touches settlement (SPEC §2, ADR-0021).

The sidecar sends no Consumer PII to Razorpay. The sidecar holds commitments to the Destination and Contact Point. Those commitments become unopenable on erasure. A copy in the dashboard of a Provider is a copy that erasure cannot reach.

## 6. Health

- `/healthz` shows that the process is up. It says nothing about work readiness.
- `/readyz` shows that configuration loaded and that the boot refused dangerous combinations. It names what is missing. It names an empty JWKS. It names an active dev SSRF allowlist.

The boot log states what the boot wired and what the boot did not wire. The shape stays the same each time. You read these items:

- the schema revision
- the database
- the Provider adapter
- the signing key
- the trait URL
- if the sweeper started

If you see `NOT CONFIGURED`, that line answers most why-did-nothing-happen questions.

## 7. The store on the other side

The sidecar needs a Merchant that serves the nine doors. You prove that fact first. Then you point real traffic at the sidecar:

```
TRAIT_HMAC_SECRET=... openstore-conform --read-only https://shop.example/trait-base
TRAIT_HMAC_SECRET=... openstore-conform https://shop.example/trait-base
```

You run the read-only check first. That check runs doors 1, 2, and 9. That check writes nothing.

Then you run the full check. The full run creates orders. The full run takes stock holds. It gives each hold back.

If you use WooCommerce, you use `integrations/woocommerce/` as the plugin that serves the doors.

## 8. What is still absent

The list below names known gaps:

- No money moved through Razorpay yet. The four network calls match the documented API. Tests use the documented envelopes. The adapter never spoke to Razorpay. You run the adapter against a `rzp_test_` key and a sandbox webhook before you point it at anything real. The first live settlement also tests the ECO/TCS boundary of ADR-0021 in practice, not on paper.
- Nothing sweeps the checkouts table. Rows stay small and bounded by order volume. No process deletes rows. Erasure under ADR-0011 deletes the order of the Merchant and the salt with it. That step makes the commitments unopenable. The row of the sidecar stays.
- No multi-process story exists for the nonce replay guard in the WooCommerce plugin without a persistent object cache. The 60-second signature window stays the outer bound in each case.
