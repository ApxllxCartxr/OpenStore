# Deploying a sidecar

One deploy, one Merchant domain (ADR-0007). This is what a real install needs
beyond `make up`, which is the demo and creates its own schema.

## Before anything

Two facts decide most of what follows:

1. **The signing keys are the shop's identity.** Agents pin them, receipts are
   sealed with them, and the verifier checks against the JWKS they publish.
   Losing them invalidates every receipt this shop has ever issued, and there is
   no recovery from anybody else's copy because nobody has one.
2. **The sidecar's database is its own.** It holds the Ledger, the Transcripts,
   the evidence and the in-flight money path. It is never the Merchant's
   database — the sidecar reaches Merchant truth through the nine doors and
   nowhere else (SPEC §5, ADR-0001).

## 1. Schema

The sidecar refuses to start against a database it has not migrated:

```
SIDECAR_DATABASE_URL=postgresql+psycopg://... alembic upgrade head
```

**Demo creates its own schema; a real deploy does not.** A process that changes
its schema as a side effect of starting is one whose rollback leaves a table the
old code cannot read, at the worst possible moment, with nobody having reviewed
the change. So a non-demo boot checks the revision and stops with the command to
run — `SchemaNotMigrated`, naming both revisions.

Deploy order is therefore: migrate, then start. Migrations that add a column are
safe to run before the old process stops; one that drops or renames is not, and
should be split across two releases.

`alembic downgrade` exists because alembic wants a pair. **Nobody should run it
on a deploy that has taken an order**: the Ledger is append-only, and that is the
one statement in this codebase that can delete it.

## 2. Secrets

Every secret is per deploy and none of them have safe defaults. `.env.example`
names all of them with no values, because a template that ships a working secret
is how that secret reaches production.

| Variable | What it is | Losing it |
|---|---|---|
| `SIDECAR_SIGNING_KEY_PATH` | the keyfile | every receipt becomes unverifiable |
| `SIDECAR_SIGNING_KEY_PASSPHRASE` | encrypts that keyfile at rest | the keyfile will not open |
| `DEPLOY_PSEUDONYM_KEY` | derives `consumer_id` | old orders stop correlating to new ones (which is also how you rotate deliberately) |
| `TRAIT_HMAC_SECRET` | signs the nine doors | every door refuses |
| `PROVIDER_WEBHOOK_SECRET` / `RAZORPAY_WEBHOOK_SECRET` | verifies the money callback | every callback is refused, and payments only finish through a manual status check |
| `RAZORPAY_KEY_ID` / `_SECRET` | the Merchant's own Razorpay account | no link can be created, so no prepaid order can be paid |
| `OAUTH_CLIENT_ID` / `_SECRET` | the allowlisted-agent route | that route refuses everyone; self-registration still works |

Two webhook names rather than one shared value, because Razorpay's is issued by
Razorpay's own dashboard: a deploy that pasted it into a generic variable would
be verifying callbacks with a secret the Provider never agreed to.

**There is no unauthenticated callback path.** With no webhook secret set, every
callback is refused — demo mode included.

Keep secrets in the platform's own store (Fly secrets, Kubernetes secrets, a
systemd credential), not in a `.env` on a shared host. The sidecar reads them as
environment variables and never writes them anywhere.

## 3. Back up the keys, and prove the backup

Set `SIDECAR_KEY_EXPORT_PATH` **before first boot**. The first boot enrols the
shop's key and writes the export once — the only moment a key exists that has
never been backed up. It is not rewritten afterwards, because an export that
followed every rotation automatically would quietly overwrite the copy the
operator has not yet moved anywhere.

Then do the half everyone skips:

```
openstore-keys check /backups/keys.json --against /var/lib/openstore/keys.json
```

It reports whether the backup opens *and* whether it holds the key the shop is
currently publishing. A backup that opens but is missing a key added by a
rotation would leave every receipt sealed since then verifying against nothing —
which is the failure this drill exists to find, and it looks fine until the day
it matters.

To make a fresh encrypted copy at any time:

```
openstore-keys export /var/lib/openstore/keys.json /backups/keys.json \
    --export-passphrase "$BACKUP_PASSPHRASE"
```

An unencrypted export is refused. The live keyfile may be unencrypted — it sits
on a host only the operator can reach — but a copy that travels to a backup does
not have that protection.

## 4. Back up the database

The Ledger and the Transcripts are the shop's own record of what it decided and
what money moved, and receipts are resolved from it by id. Ordinary Postgres
backups are correct here; what matters is that **the keyfile and the database
are backed up together**. A database without its keys holds receipts nobody can
verify; keys without the database hold an identity with nothing to prove.

## 5. The payment rail

`PAYMENT_PROVIDER=razorpay` talks to the Merchant's own Razorpay account over
the Payment Links API. Two things about it are worth knowing before the first
deploy:

- **The adapter refuses to boot on a live key while `OPENSTORE_DEMO_MODE` is
  on.** Every demo receipt is marked demo, and a demo that can move real money
  is not a demo.
- **The link lives slightly longer than §16.7's fifteen-minute window**, because
  Razorpay refuses an `expire_by` that is not *more than* fifteen minutes out.
  The sidecar takes the Provider's own lifetime as the ceiling on the stock
  hold, so the longer link shortens nothing.

Settlement runs Consumer → the Merchant's own account, untouched by the sidecar
(SPEC §2, ADR-0021). No Consumer PII is sent to Razorpay: the sidecar holds
commitments to the Destination and Contact Point that become unopenable on
erasure, and a copy in a Provider's dashboard is a copy erasure cannot reach.

## 6. Health

- `/healthz` — the process is up. Says nothing about whether it can work.
- `/readyz` — configuration loaded and the dangerous combinations refused. It
  names what is missing: an empty JWKS, an active dev SSRF allowlist.

The boot log states what was wired and what was not, in the same shape every
time: the schema revision, the database, the Provider adapter, the signing key,
the trait URL, and whether the sweeper started. A line reading `NOT CONFIGURED`
is the answer to most "why did nothing happen" questions.

## 7. The store on the other side

The sidecar needs a Merchant implementing the nine doors. Prove it before
pointing real traffic at it:

```
TRAIT_HMAC_SECRET=... openstore-conform --read-only https://shop.example/trait-base
TRAIT_HMAC_SECRET=... openstore-conform https://shop.example/trait-base
```

Read-only first: it runs doors 1, 2 and 9 and writes nothing. The full run
creates orders and takes stock holds, and gives every hold back.

For WooCommerce, `integrations/woocommerce/` is a plugin that serves the doors.

## 8. What is still absent

Named rather than discovered:

- **No money has moved through Razorpay.** The four network calls are
  implemented against the documented API and tested against its documented
  envelopes, but this adapter has never spoken to Razorpay. Run it against a
  `rzp_test_` key and a sandbox webhook before pointing it at anything real; the
  first live settlement is also the first test of ADR-0021's ECO/TCS boundary in
  practice rather than on paper.
- **Nothing sweeps the checkouts table.** Rows are small and bounded by order
  volume, but they are never deleted; erasure (ADR-0011) deletes the Merchant's
  order and the salt with it, which makes the commitments unopenable, but the
  sidecar's own row stays.
- **There is no multi-process story for the nonce replay guard** in the
  WooCommerce plugin without a persistent object cache. The 60-second signature
  window is the outer bound either way.
