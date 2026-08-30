# Production Readiness — OpenStore

**What separates this demo from something a payments company could actually run.**

Audience: a payments engineer who has been paged at 3am because a webhook was replayed
out of order and a customer got charged twice.

The Intent Compiler is the *thesis*. This document is the *engineering*. It is organised
in three passes:

1. **Part 0** — defects on the money path in the code as it stands today, found by
   red-teaming my own implementation. File and line for each, the exploit, the fix.
2. **Parts 1–5** — the machinery a real agentic-commerce merchant server needs that this
   one does not have yet: idempotency semantics, the dual-write problem, a double-entry
   spend ledger, webhook discipline, reconciliation, key management, regulatory fit.
3. **Parts 6–8** — how it gets tested, what I would build in the remaining hours, and
   the demo beats that make a payments engineer believe it.

Everything marked **`[verify]`** is a claim about an external system (Razorpay API shapes,
RBI circulars, protocol drafts) that must be checked against the primary source before it
goes in a submission. I have flagged them rather than silently asserting them.

---

## Part 0 — Defects on the money path, in this repo, today

I reviewed my own `checkout_confirm` as if it were someone else's PR. Six findings. Four
are exploitable by the agent the system is explicitly designed to distrust.

| # | Severity | Where | What breaks |
|---|---|---|---|
| 1 | **Critical** | `merchant/mcp_server.py:160-186` | The WebAuthn assertion is never verified on the money path |
| 2 | **Critical** | `merchant/mcp_server.py:187-192` | Cart can be swapped between `initiate` and `confirm` |
| 3 | **High** | `merchant/oauth/routes.py:24` | Access tokens are HS256 under a hardcoded secret; advertised JWKS endpoint does not exist |
| 4 | **High** | `merchant/intent_routes.py:95-135` | `/internal/webauthn/*` is unauthenticated — anyone can enrol a passkey with their own policy |
| 5 | **High** | `merchant/mcp_server.py:145-155`, `_persist_confirm_result` | Idempotency is a read-then-write race with no unique index and no request fingerprint |
| 6 | **Medium** | `merchant/mcp_server.py:196-210` | Razorpay is called before the local commit — crash between the two orphans a live payment link |

### 0.1 — The assertion is never verified where it matters

`merchant/webauthn.py:40` defines `verify_assertion_policy()`. Grep the repo: **nothing
calls it.** The only place a signature is actually checked with `fido2` is
`/internal/webauthn/complete-signing` (`merchant/intent_routes.py:175`), which runs during
the browser ceremony.

What `checkout_confirm` Path A actually does:

```python
credential_id = policy_token.get("rawId") or policy_token.get("id", "")   # agent-supplied
policy_row = session.exec(select(IntentPolicyRow)
    .where(IntentPolicyRow.credential_id == credential_id)).first()
# ...then compares hash(policy_row.policy_json) to hash(policy_json)
```

There is no signature check, no challenge check, no origin check. `policy_token` is treated
as an opaque bag from which one string is read.

**Exploit.** A compromised or prompt-injected agent calls:

```json
{"checkout_id": "…", "policy_token": {"rawId": "<any credential id it has seen>"},
 "policy_json": {…the policy it already legitimately received…}}
```

No private key, no authenticator, no human. It passes. The entire cryptographic claim of
the project — "a prompt-injected agent cannot forge this" — is currently false at the one
place it is load-bearing. The credential ID is not a secret; it is returned to the agent by
`/internal/webauthn/latest-assertion`.

**Fix.** Call the verifier that already exists, from inside the money path, before the
compiler runs:

```python
challenge_b64 = json.loads(b64url_decode(
    policy_token["response"]["clientDataJSON"]))["challenge"]
challenge_row = session.exec(
    select(PolicyChallenge).where(PolicyChallenge.challenge_id == challenge_b64)).first()
if challenge_row is None:
    raise HTTPException(403, "assertion_unknown_challenge")

verify_assertion_policy(
    credential_id_b64=policy_row.credential_id,
    public_key_b64=policy_row.public_key,
    assertion_b64=policy_token,
    policy_json=policy_json,
    nonce_to_policy_hash={challenge_b64: challenge_row.policy_hash},
)
```

And add the regression test that would have caught it — see §6.2, `test_forged_policy_token_rejected`.

> **This finding is a demo asset, not an embarrassment.** "I built the cryptographic gate,
> then reviewed my own money path and found I had wired the verifier to the ceremony but
> not to the transaction. Here is the exploit, here is the fix, here is the test that fails
> on the old code." Every payments engineer in the room has shipped this class of bug. Very
> few candidates find their own and say so.

### 0.2 — The compiler validates one cart; Razorpay charges for another

`checkout_initiate` freezes `cart_hash` and `total_minor` onto the `Checkout` row
(`merchant/checkout.py:41-53`). Path B — the legacy mandate path — re-checks that hash at
`mcp_server.py:246`. **Path A never does.** It re-reads `cart.items_json` live:

```python
cart = session.get(Cart, checkout.cart_id)
ok, reason = verify_cart_against_policy(cart_items=cart.items_json, …)
…
rp_result = create_order_and_payment_link(checkout.total_minor, checkout.checkout_id)
```

The compiler verifies the *current* cart. Razorpay is charged the *frozen* total. Those are
two different objects.

**Exploit.** `create_cart` with 5 × ₹100 items → `checkout_initiate` freezes
`total_minor = 50000` → `update_cart` down to 1 × ₹100 → `checkout_confirm`. The compiler
sees a ₹100 compliant cart and waves it through; the customer is charged ₹500. The cart
version bump that the README advertises as the anti-tampering control is computed but never
compared to anything on this path.

**Fix.** The compiler must run over the frozen snapshot, not the live cart, and the frozen
snapshot must be stored on the checkout rather than fetched by foreign key:

```python
# models.py — freeze at initiate, never re-read the mutable cart afterwards
class Checkout(SQLModel, table=True):
    ...
    cart_snapshot_json: list[dict] = Field(sa_column=Column(JSON))
    cart_version: int

# mcp_server.py — Path A
if compute_cart_hash(checkout.cart_snapshot_json) != checkout.cart_hash:
    raise HTTPException(409, "checkout_snapshot_corrupt")
ok, reason = verify_cart_against_policy(cart_items=checkout.cart_snapshot_json, …)
assert sum(i["unit_minor"] * i["qty"] for i in checkout.cart_snapshot_json) \
    == checkout.total_minor, "amount_snapshot_divergence"
```

The general rule, worth stating explicitly in the README because it is the rule the whole
industry converged on: **the object you authorise, the object you verify, and the object you
charge must be the same immutable object, identified by hash.** A foreign key to a mutable
row is not that.

### 0.3 — `checkout.expires_at` is written and never read

`merchant/checkout.py:52` sets a 5-minute expiry. No code path compares it to the clock.
Neither confirm path checks it. A checkout initiated on Monday confirms on Friday.

```python
if checkout.expires_at < datetime.now(timezone.utc):
    checkout.status = "EXPIRED"; session.commit()
    raise HTTPException(409, "checkout_expired")
```

Related: the codebase uses `datetime.utcnow()` throughout, which is deprecated in 3.12+ and
returns a naive datetime. Every timestamp comparison in this system is naive-local-vs-naive-
UTC by luck. Move to `datetime.now(timezone.utc)` and store tz-aware.

### 0.4 — Symmetric tokens, hardcoded secret, phantom JWKS

```python
JWT_SECRET = "dev-only-change-me"   # merchant/oauth/routes.py:24
```

All access tokens are HS256 signed with a constant that is committed to git. Anyone who
reads the repository can mint a token with `checkout:confirm` scope. Separately, the AS
metadata advertises `jwks_uri: /oauth/jwks.json` (`routes.py:38`) and **no such route is
registered** — a client doing correct discovery-driven verification gets a 404.

The README claims "Access tokens: JWT, 15-min, scoped, `kid`-pinned. `/oauth/jwks.json`."
That is the plan's claim, not the code's behaviour. Either implement it or delete the claim;
a judge who curls the advertised endpoint and gets a 404 discounts everything else you said.

**Fix** (~40 lines): ES256 or EdDSA keypair loaded from env/file, `kid` header, real
`/oauth/jwks.json` serving the public JWK, `mcp_auth.py` and `intent_routes.verify_token`
verifying against the public key with an algorithm allowlist. You already do the algorithm
allowlist correctly for mandates (`merchant/mandate.py:8, 71-73`) — reuse that discipline.
Asymmetric tokens also mean the resource server never holds signing material, which is the
argument you want to be able to make out loud.

### 0.5 — The enrolment ceremony has no authentication

`/internal/webauthn/challenge`, `/register`, `/begin-signing`, `/complete-signing` take no
credentials at all (`merchant/intent_routes.py:68-235`). `user_id` is a request body field
defaulting to `"default-user"`. `/internal/otp-verify` is likewise open.

**Exploit.** Anyone who can reach the server enrols their own passkey against
`user_id="default-user"` with `max_spend_minor: 10_000_000`, signs it, and it becomes the
row `latest-assertion` hands out (`ORDER BY created_at DESC`). The human's policy is
replaced by the attacker's. The entire trust model rests on an endpoint with no auth.

**Fix.** These are human-session endpoints, not agent endpoints. They need a signed session
cookie established by a merchant login, `SameSite=Strict`, CSRF token on the POSTs, and
`user_id` derived from the session — never from the body. Bind `/internal/*` to a separate
router with a session dependency and assert in a test that no route under `/internal/`
resolves without one.

### 0.6 — Idempotency is a race, not a guarantee

```python
existing = session.exec(select(IdempotencyRecord)
    .where(...client_id...).where(...idempotency_key...)).first()
if existing is not None:
    return existing.response_json
# ... 40 lines later, after calling Razorpay ...
session.add(IdempotencyRecord(...))
```

Three separate problems, all of them things a payment gateway gets graded on:

- **No uniqueness constraint.** `IdempotencyRecord` has plain indexes on `client_id` and
  `idempotency_key` (`models.py:158-163`), not a composite unique. Two concurrent confirms
  both read empty, both call Razorpay, both insert. Two payment links, one cart.
- **No request fingerprint.** Reusing a key with a *different* body silently returns the old
  response. Stripe returns an error in this case; so should you. Otherwise a buggy agent that
  recycles keys gets a confirmation for a purchase that never happened.
- **No in-flight state.** A key is either absent or complete. There is no "in progress"
  marker, so a retry that arrives while the first call is still talking to Razorpay
  duplicates the charge — which is exactly the retry pattern the `/demo/inject-timeout` beat
  is designed to provoke.

The correct shape is in §1.1.

### 0.7 — Razorpay is called before the local write commits

```python
rp_result = create_order_and_payment_link(...)     # external, irreversible
result = _persist_confirm_result(...)              # local, may fail
```

Process dies in between → a live Razorpay order and payment link exist with no local `Order`
row, no `IdempotencyRecord`, no `SpendLedgerEntry`, and a checkout still in
`POLICY_VERIFIED`. The agent retries with the same key, the key is absent, and a **second**
payment link is created for the same cart. This is the classic dual-write problem and it is
the single most common way real integrations lose money. §1.2.

### 0.8 — Smaller, still worth fixing

- **Spend-cap TOCTOU.** `rolling_spend_minor()` reads, then the ledger row is written much
  later without a lock (`mcp_server.py:194`, `policy.py:49`). N concurrent confirms each see
  the pre-state and each pass a cap they collectively blow through. SQLite needs
  `BEGIN IMMEDIATE` on the confirm transaction; Postgres needs `SELECT … FOR UPDATE` on a
  per-client budget row.
- **Rate limiter is in-process memory.** `policy.py:26` `_BUCKETS` is a module-level dict.
  It resets on restart and does not exist across workers — so `--workers 4` multiplies every
  money-path limit by four. Money-touching limits belong in the database or Redis.
- **Audit log records `client_id=None` on every row.** `audit.py:22` reads
  `kwargs.get("_client_id_for_audit")`, which nothing ever sets. The audit trail — a named
  deliverable — cannot attribute any call to any client.
- **Audit `trace_id` does not correlate.** The decorator mints its own UUID
  (`audit.py:17`) while `checkout_initiate`/`verify_cart_against_policy` mint others. The
  README promises "`trace_id` in every footer … correlating all three processes"; today the
  same logical request appears under three unrelated IDs. Generate one at ingress and thread
  it through — a `contextvars.ContextVar` is the least invasive fix.
- **PII in the audit log.** `args_json` stores the full kwargs, which includes
  `delivery_address` and the entire WebAuthn assertion. Redact by allowlist, not denylist.
- **`tags` semantics are permissive.** `intent_compiler.py:56` passes an item if
  `item_tags ∩ allowed_tags ≠ ∅`. An item tagged `["vegan", "alcohol"]` satisfies a
  `["vegan"]` policy. For a *safety* allowlist you almost certainly want
  `item_tags ⊆ allowed_tags`, or an explicit `required_tags` / `forbidden_tags` split. State
  which semantics you chose and why — this is the kind of thing a reviewer will ask.
- **`max_spend_minor` is per-checkout, not per-policy.** See §2.3 — this is a thesis-level
  hole, not a nit.
- **No schema migrations.** `init_db()` is `create_all`. Any column added after a demo
  recording silently does not exist in `openstore.db`. Alembic, one command, before you have
  data you care about.

---

## Part 1 — The money path, done the way a gateway does it

### 1.1 Idempotency as a contract, not a cache

An idempotency key is a promise: *"the same key with the same request produces the same
outcome exactly once, no matter how many times you send it or what fails in between."*
Delivering that needs four things the current code lacks.

```python
class IdempotencyRecord(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("client_id", "idempotency_key"),)
    client_id: str
    idempotency_key: str
    request_fingerprint: str          # sha256(canonical(semantic request fields))
    state: str                        # IN_FLIGHT | COMPLETED | FAILED
    response_json: dict | None
    status_code: int | None
    created_at: datetime
    expires_at: datetime              # 24h; keys are not immortal
```

The confirm handler becomes:

```python
fingerprint = sha256(canonical_json_bytes({
    "checkout_id": checkout_id, "cart_hash": checkout.cart_hash,
    "amount_minor": checkout.total_minor,
})).hexdigest()

try:
    with session.begin():                       # BEGIN IMMEDIATE on SQLite
        session.add(IdempotencyRecord(client_id=..., idempotency_key=key,
                                      request_fingerprint=fingerprint,
                                      state="IN_FLIGHT", expires_at=now+24h))
except IntegrityError:
    rec = fetch(client_id, key)
    if rec.request_fingerprint != fingerprint:
        raise HTTPException(422, "idempotency_key_reuse_with_different_payload")
    if rec.state == "IN_FLIGHT":
        raise HTTPException(409, "request_in_progress")   # retriable, Retry-After: 1
    return rec.response_json
```

Four properties you can now state in one sentence each, which is how you want to present
this: **unique per (client, key)** so concurrency collapses to one winner;
**fingerprinted** so key reuse with a different cart is an error rather than a silent lie;
**in-flight-aware** so a retry during the first attempt gets 409 rather than a second
charge; **expiring** so the table is bounded and a key cannot be replayed a year later.

Scope note worth saying out loud: the key is scoped to `client_id`. Two different agents
cannot collide, and one agent cannot replay another's key. That is deliberate.

### 1.2 The dual-write problem: intent-first, then outbox

Never call an external money API from inside a code path whose local effects have not been
durably recorded. The order is always:

```
1. BEGIN            write IdempotencyRecord(IN_FLIGHT) + Checkout→CONFIRMING
                    write PspIntent(checkout_id, amount, idempotency_key, state=PENDING)
   COMMIT           ← now a crash is recoverable: the intent survives
2. call Razorpay    with reference_id = checkout_id (deterministic, not random)
3. BEGIN            PspIntent→SUCCEEDED, Order row, ledger CAPTURE,
                    IdempotencyRecord→COMPLETED, Checkout→ORDER_CREATED
   COMMIT
```

If the process dies after step 1, a `PspIntent` row sits in `PENDING`. A recovery worker
picks it up, and because `reference_id` is derived deterministically from `checkout_id`, it
can ask Razorpay "does a payment link with this reference already exist?" and adopt it
instead of creating a second one. `[verify]` Razorpay payment links treat `reference_id` as
unique — if a duplicate create returns a specific error code, catch *that code specifically*
and fetch-then-adopt. Do not catch bare `Exception`; that is how you turn a duplicate-detect
into a duplicate-create.

Deterministic external IDs are the cheapest idempotency you will ever get, because they push
the uniqueness constraint into the system that actually owns the money.

Same discipline for the Discord/notifier side effects: an outbox table, written in the same
transaction as the state change, drained by a worker. A notification that fails to send must
never roll back a payment, and a payment that succeeds must never lose its notification.

### 1.3 The spend ledger should be double-entry, and it should reverse

Today: one row appended at order creation (`mcp_server.py`, `_persist_confirm_result`), never
amended. Two consequences:

- The 24h cap counts **intent to pay**, not money spent. A customer who abandons every
  payment link still burns their entire daily budget. There is no release path.
- A refund, a failed payment, or an expired link is invisible to the cap.

Model it as an append-only journal with typed entries and a derived balance:

```python
class LedgerEntry(SQLModel, table=True):
    entry_type: str      # RESERVE | CAPTURE | RELEASE | REFUND
    checkout_id: str     # correlation
    policy_hash: str     # which signed policy authorised this — see §2.3
    client_id: str
    amount_minor: int    # always positive; entry_type carries the sign
    created_at: datetime

def available_minor(session, client_id, window_h=24) -> int:
    # RESERVE and CAPTURE consume budget; RELEASE and REFUND return it
    ...
```

`RESERVE` at confirm, `CAPTURE` on `payment_link.paid`, `RELEASE` on link expiry or
`payment.failed`, `REFUND` on refund webhook. Append-only means the ledger is an audit
artifact: you can replay it to any point in time and explain every rupee. Never `UPDATE` a
ledger row — that is the whole point of the pattern, and it is the thing that makes a
finance team trust the system.

**Invariant test** (§6.4): for any interleaving of events, `sum(RESERVE) + sum(CAPTURE) -
sum(RELEASE) - sum(REFUND)` equals the outstanding exposure, and never goes negative.

### 1.4 Webhooks: assume at-least-once, out-of-order, and occasionally never

`merchant/webhooks.py` gets signature verification right — raw body, HMAC-SHA256,
`hmac.compare_digest`. Everything after that needs work.

**`payload["id"]` is probably not a field.** `[verify]` Razorpay's webhook body is
`{entity, account_id, event, contains, payload, created_at}` — I do not believe there is a
top-level `id`, which would make `webhooks.py:33` raise `KeyError` on every real delivery and
return 500, causing Razorpay to retry forever. The documented idempotency handle is the
`X-Razorpay-Event-Id` header. Capture one real webhook with `cloudflared` + `tee` and read it
before trusting either of us:

```python
event_id = request.headers.get("x-razorpay-event-id") \
        or sha256(raw_body).hexdigest()      # deterministic fallback, never random
```

This is exactly the "look at real data before writing pipeline code" rule, and it is worth
saying in the demo: *"I captured a live webhook and built against the bytes, not the docs."*

**Ordering.** Delivery order is not event order. `payment.captured` can land before
`payment_link.paid`. Guard every transition with an explicit allowed-transition table and
make terminal states absorbing:

```python
TERMINAL = {"PAID", "REFUNDED", "FAILED"}
ALLOWED = {("CREATED","PAID"), ("CREATED","FAILED"), ("PAID","REFUNDED")}
if order.status in TERMINAL and (order.status, new) not in ALLOWED:
    log.info("webhook.ignored_stale", ...); return {"status": "ignored_stale"}
```

Also compare `payload["created_at"]` against `order.status_updated_at` and drop events older
than the current state — the cheapest defence against reordering.

**Acknowledge fast, process durably.** Verify signature → persist raw event → return 200.
Process in a worker off the raw row. A slow order-transition should not cause a webhook
timeout and a redelivery storm. And return non-2xx *only* when you want a retry: today a
processing failure inside the handler returns 500 and Razorpay retries a poisoned event
indefinitely. Bound it — after N attempts, move the event to a dead-letter table with the
error, alert, and stop.

**Replay tooling.** Keep the raw payloads (you do) and add
`python -m merchant.replay <event_id>` that re-runs the processor against the stored bytes.
This turns "we lost an event in production" from an archaeology project into one command,
and it makes your webhook processor testable against a corpus of real captures.

### 1.5 Reconciliation — because webhooks get lost

Every payments company on earth runs a sweeper, because at-least-once delivery is still not
*at-least-once-eventually-guaranteed*. Tunnels drop. Deploys restart mid-POST.

```
every 5 min:
  for order in orders where status='CREATED' and age between 10m and 7d:
      psp = razorpay.payment_link.fetch(order.razorpay_payment_link_id)
      if psp.status != local_status: emit drift event, apply the PSP's answer, alarm
```

Two properties that matter:

- **The PSP is the source of truth for money.** Local state is a cache of it. When they
  disagree, the PSP wins, and the disagreement is an alarm, not a log line.
- **`reconciliation_drift_total` is your most important metric.** A healthy system reports
  zero. A non-zero value means webhooks are being lost and you would otherwise not know.

Being able to say *"here is my drift counter, it is zero, here is the sweeper that keeps it
zero"* is a stronger statement than any feature on the happy path.

### 1.6 Error taxonomy

Every rejection currently returns a prose string in an `HTTPException` detail. Agents cannot
branch on prose, humans cannot aggregate it, and dashboards cannot chart it. Give every
rejection a stable machine code, and return it in a consistent envelope:

```json
{"error": {"code": "policy.spend_cap_exceeded", "message": "Cart total ₹720.00 exceeds policy limit ₹500.00",
           "retriable": false, "trace_id": "…", "details": {"total_minor": 72000, "limit_minor": 50000}}}
```

Namespaces: `auth.*`, `policy.*` (the compiler's verdicts), `checkout.*`, `psp.*`,
`ratelimit.*`. `retriable` tells the agent whether to back off or give up — an agent that
retries a `policy.*` rejection is burning your rate limit for nothing, and an agent that
gives up on `psp.timeout` loses a sale. This is a small change with an outsized effect on
how the system reads to a reviewer.

---

## Part 2 — The cryptographic path, done properly

### 2.1 What the signature currently authorises, and what it should

Today's model: the human signs a *policy*. The resulting assertion is stored
(`AssertionRow`) and handed to the agent by `latest-assertion`, which returns the newest row
forever. That assertion is:

- reusable for an unbounded number of checkouts
- not bound to any `checkout_id`, `cart_hash`, or amount
- never expired, never rotated, never revoked
- verified at ceremony time and — per §0.1 — not at spend time

Reusability is a legitimate design choice; it is what makes the system "one signature, many
purchases," which is the whole pitch. But then the artifact needs the properties of a
*credential*, and it has none of them. The industry answer is a two-tier structure, and it
is worth adopting explicitly because it is what AP2 and the card networks describe:

| Tier | Object | Signed by | Lifetime | Binds |
|---|---|---|---|---|
| 1 | **Intent Mandate** | human, WebAuthn | hours–days, revocable | the policy: caps, tags, merchant, expiry |
| 2 | **Cart Mandate** | merchant server, EdDSA | 120s, single-use | this exact cart hash, amount, checkout |

Tier 1 is what your ceremony already produces. Tier 2 is what `merchant/mandate.py` already
builds — you wrote it for the OTP path and then bypassed it. The Intent Compiler is the
*function that derives tier 2 from tier 1*: it takes the human's signed policy and a frozen
cart and, if the cart is inside the policy, emits a single-use mandate that authorises that
one transaction and nothing else.

That reframing costs you almost no code — both halves exist — and it buys you: replay
protection (the `jti` burn you already implemented, `mcp_server.py:250-256`), a 120s window
on the authorisation that actually touches money, a per-transaction artifact for the audit
trail, and direct vocabulary alignment with AP2's Intent Mandate / Cart Mandate split. It
also fixes the "what exactly did the human authorise?" question, which is the first thing a
payments person will ask you.

### 2.2 Assertion hygiene the current code skips

- **`sign_count` is stored and never compared.** `IntentPolicyRow.sign_count` is written at
  registration (`intent_routes.py:127`) and never read. WebAuthn's signature counter exists
  to detect cloned authenticators: if an assertion arrives with a counter ≤ the stored value
  (and the authenticator reports counters at all — many passkeys report 0), you have evidence
  of cloning. Compare, update, and alarm on regression.
- **`UV` flag unchecked.** `authenticatorData` carries a user-verification bit. For a
  transaction authorisation you want to *require* UV — that is the difference between "the
  device was present" and "the human authenticated to the device." Set
  `user_verification=REQUIRED` in the ceremony and assert the flag server-side; do not trust
  the ceremony to have enforced it.
- **No revocation path.** `IntentPolicyRow.active` exists. Nothing ever sets it False. A
  human who loses their laptop has no way to kill the policy. One endpoint, one button on
  the storefront, one test.
- **RP ID is hardcoded to `localhost`** (`webauthn.py:18`) — already in the README's
  "not done" list. Parameterise from settings; note that RP ID is a security boundary (an
  eTLD+1 scope), so this is config, not a constant.
- **Registration state looks wrong.** `register_complete(challenge_row.challenge_id, credential)`
  passes a bare nonce string where `fido2` expects the state dict returned by
  `register_begin()`. The signing path does this correctly (`begin_signing` stores
  `state_json`, `complete_signing` passes it back). `[verify]` — exercise the registration
  ceremony end-to-end in a browser and confirm attestation is actually being verified rather
  than throwing into the generic `except Exception` at `intent_routes.py:115`. A ceremony
  that always fails and a ceremony that never validates look identical from the outside.
- **Challenge TTL is 10 minutes.** Fine for enrolment, generous for a spend authorisation.
  60–120s for signing.

### 2.3 The policy has no budget — the thesis-level gap

`IntentPolicy.max_spend_minor` is checked per cart (`intent_compiler.py:66-75`). Nothing
tracks cumulative spend against a policy. An agent holding a valid ₹500 policy can run
twenty ₹499 checkouts.

The rolling 24h cap in `policy.py` partially masks this, but it is keyed on `client_id`, not
on `policy_hash` — so it is a *merchant's* rate limit on an *agent*, not the *human's* budget
on their own delegation. Those are different controls answering to different parties, and
conflating them is exactly the kind of thing that gets caught in a design review.

The policy is a *budget*, not a *per-transaction limit*. Add both, name them clearly, and let
the human set them at the ceremony:

```python
class IntentPolicy(BaseModel):
    max_spend_per_tx_minor: int
    max_spend_total_minor: int        # cumulative across the policy's life
    max_transactions: int             # a count cap is a cheap, legible second dimension
    allowed_tags: list[str]
    tag_mode: Literal["any", "all"] = "all"     # §0.8 — make the semantics explicit
    blocked_skus: list[str] = []
    merchant_id: str
    not_before: int
    expires_at: int
    policy_version: int = 1           # so the format can evolve without ambiguity
```

Enforce the cumulative cap by querying `LedgerEntry` filtered on `policy_hash` — which is
why §1.3's ledger carries that column. Then the compiler's verdict becomes a genuinely
complete statement: *this cart, against this policy, given everything already spent under it.*

Surface `remaining_minor` in the `checkout_initiate` response so the agent can reason about
its own budget instead of discovering the ceiling by hitting it. Agents that know their
limits make fewer doomed calls, which is both better UX and lower load.

### 2.4 Canonicalisation is load-bearing — treat it that way

`canonical_json_bytes` (`mandate.py:29`) is `sort_keys=True, separators=(",",":"),
ensure_ascii=False`. The policy hash that binds a signature to a policy is a SHA-256 over
that output. If two parties disagree by one byte, verification fails; if an attacker finds
two policies with the same canonical form, verification succeeds when it should not.

- `ensure_ascii=False` means the output is UTF-8-encoded non-ASCII. Fine, but Unicode
  normalisation is not applied — `"café"` in NFC and NFD produce different bytes and
  therefore different hashes for a semantically identical policy. Normalise to NFC before
  encoding, or constrain policy strings to ASCII and reject the rest at the boundary.
- Floats must be impossible by construction, not by convention. Every monetary field is an
  `int` of minor units — assert it: `assert all(isinstance(v, int) for v in money_fields)`.
- The plan cites RFC 8785 (JCS). What is implemented is *RFC-8785-style*, not JCS —
  it differs on number serialisation and does not do Unicode normalisation. Say
  "canonical JSON (sorted keys, tight separators, integer minor units)" and describe it
  precisely, or use a JCS library. Do not claim a spec you have not implemented; a reviewer
  who knows JCS will check.
- Pin it with **golden vectors**: a committed fixture of `(policy_dict, expected_sha256)`
  pairs including unicode, empty lists, and key-order permutations. If a refactor changes
  canonicalisation, every previously signed policy silently stops verifying — golden vectors
  are the only thing that catches that in CI.

---

## Part 3 — Fitting Indian payments reality

This is the section that separates "a clever hack" from "someone who understands the
business Razorpay is in." Every claim here is `[verify]` — check the current circular text
before it goes in a submission, because thresholds and effective dates move.

### 3.1 Additional Factor of Authentication

Indian card-not-present payments have required AFA for years, in practice almost always
SMS OTP. `[verify]` RBI has since moved toward a principle-based framework that permits
authentication factors beyond OTP — explicitly contemplating device-bound and biometric
factors — with risk-based application.

That framing is *exactly* what the Intent Compiler implements: a **device-bound,
possession-plus-inherence factor** (the passkey's private key never leaves the authenticator;
user verification is biometric or PIN), which is strictly stronger than an SMS OTP that can
be SIM-swapped or phished. Your own `plan.md` §7 already concedes that DM+OTP "is not
phishing-resistant under NIST SP 800-63B-4." The WebAuthn path is the fix to your own stated
limitation, and you should say so in exactly those terms — it shows the design moved for a
reason.

The honest boundary, which you must state: OpenStore performs AFA at the **merchant**
authorisation layer. It does not replace the AFA the **issuer** performs on the card
transaction itself. What you have built is a delegation-authorisation factor, not an issuer
authentication factor. Claiming otherwise to a Razorpay engineer would be an instant loss of
credibility; drawing the line yourself is a gain.

### 3.2 The e-mandate analogy — the strongest positioning available

`[verify]` RBI's e-mandate framework for recurring transactions works like this: AFA is
performed **once, at mandate registration**; subsequent debits within the mandate's limits
proceed **without** per-transaction AFA, subject to a pre-debit notification to the customer
and an accessible revocation path. Above a threshold amount, AFA is required per debit.

Map it directly onto your architecture:

| e-mandate | OpenStore |
|---|---|
| Mandate registration with AFA | WebAuthn policy signing ceremony |
| Mandate limits (amount, validity, frequency) | `IntentPolicy` fields (§2.3) |
| Debit within limits, no AFA | Intent Compiler verifies, checkout proceeds |
| Pre-debit notification | **missing** — build it |
| Above-threshold debit needs AFA | **missing** — build it (step-up) |
| Revocation | **missing** — build it (§2.2) |

Those three gaps are each an hour of work and each is a *regulatorily-motivated feature*,
which is a much better thing to demo than another happy-path polish:

1. **Pre-debit notification.** Before `checkout_confirm` executes, the merchant bot DMs the
   human: "Your agent is about to spend ₹420 at Gelateria Roma under the policy you signed
   on Aug 26. [Freeze this policy]." Notification, not approval — it does not block. This is
   the control that makes autonomy socially acceptable, and it is the answer to "what if the
   agent goes rogue at 3am?"
2. **Step-up threshold.** `IntentPolicy.step_up_above_minor`. Under it, the compiler decides
   alone. Over it, the checkout parks and requires a fresh per-transaction WebAuthn assertion
   bound to that specific `cart_hash`. **This is your best demo beat of the entire project**:
   the same agent, the same policy, two carts — ₹400 sails through untouched, ₹4,000 stops
   dead and demands the human's fingerprint on that exact cart. It shows you understand that
   the answer is not "always autonomous" or "always ask," but *risk-proportionate
   authentication* — which is precisely the direction the regulation moved.
3. **Revocation.** A button that sets `active=False`, plus the test proving the next
   `checkout_confirm` fails closed with `policy.revoked`.

### 3.3 PCI DSS scope — say the sentence

By using Razorpay payment links, no card data ever enters OpenStore. No PAN is transmitted,
processed, or stored; the cardholder enters card details on Razorpay-hosted pages.
`[verify]` this keeps a merchant in the lightest SAQ-A category.

It is one sentence in the README and it tells a payments engineer you know what compliance
scope *is*. It also justifies an architectural decision you already made — payment links
rather than a custom checkout — as a deliberate scope-reduction rather than a shortcut.

Corollary discipline, worth auditing before you record: nothing card-related, no token, no
secret, no full assertion may appear in `emit()` output or the audit log. Your trace channels
are a Discord server; treat them as a public surface. The mandate design already gets this
right (`fingerprint = sha256(jws)[:8]`, full JWS never in chat). Extend the same rule to the
WebAuthn assertion and the delivery address.

### 3.4 The India-specific things worth a line each

- **UPI is the volume.** Payment links cover UPI, and `[verify]` UPI Autopay is the
  recurring-mandate rail most Indian consumers actually use. A sentence acknowledging that
  the real deployment target for an intent policy is UPI Autopay rather than cards shows
  market awareness. `[verify]` RBI has also discussed delegated-payment constructs on
  UPI — if that framework is live, it is the closest regulatory analogue to what you built.
- **Amounts are integer paise, everywhere.** You do this. Assert it at the Razorpay boundary
  (`assert isinstance(amount_minor, int) and amount_minor > 0`) and never let a float within
  three call frames of money.
- **`receipt` is length-bounded.** `[verify]` Razorpay's order `receipt` has a 40-character
  limit; a UUID4 string is 36, so `checkout_id` fits — but that is luck, not design. Assert
  the length so a future ID-format change fails loudly in a test instead of quietly at the
  gateway.
- **Settlement is T+n, and test mode has none.** Your `plan.md` already concedes this for
  finance Q&A. Extend it: a real merchant server would reconcile against settlement reports,
  not just payment status, and the two answer different questions ("did it succeed?" vs "did
  I get the money?").

---

## Part 4 — Standards interop, concretely

The plan cites AP2, Mastercard Verifiable Intent, and Visa TAP as positioning. Positioning is
worth less than a mapping table and a shim. `[verify]` all protocol details against current
specs — this space moves monthly.

| Concept | AP2 | OpenStore |
|---|---|---|
| Human's delegated authority | Intent Mandate | WebAuthn-signed `IntentPolicy` |
| Specific transaction authorisation | Cart Mandate | Ed25519 JWS (`mandate.py`) — wire it into Path A per §2.1 |
| Verifiable presentation | VC / JWT-VC | Bare assertion + policy JSON |
| Agent identity | DID | OAuth 2.1 `client_id` |

Two honest divergences to name rather than hide: OpenStore's mandates are JWS rather than
W3C Verifiable Credentials, and agent identity is an OAuth client ID rather than a DID. Both
are deliberate — they are what is buildable and debuggable in nine days, and both are
one adapter away. Ship `merchant/interop/ap2_adapter.py` that emits your mandate as an
AP2-shaped JSON object, even if nothing consumes it. **A conformance shim is a claim you can
run; a citation is a claim you can only make.**

Also worth 30 minutes: read the Agentic Commerce Protocol (OpenAI/Stripe) and x402 (HTTP 402
+ stablecoin settlement) and write two sentences on where OpenStore sits relative to each.
Knowing that ACP takes a delegated-payment-token approach and x402 takes a
pay-per-request approach — and that OpenStore's policy-compiler model is a third
point in that space — is the difference between "I read a press release" and "I understand
the design space."

Finally: your `.well-known/agent-commerce.json` is currently a bespoke shape. Publish the
schema, version it (`"version": "0.1"` is already there — good), and state the compatibility
promise. The pitch is "a standard for the merchant side"; a standard with an unpublished,
unversioned schema is not one.

---

## Part 5 — Operability

### 5.1 SLOs, and metrics that are not vanity

| Signal | Target | Why it is the right one |
|---|---|---|
| `checkout_confirm` p99 | < 800 ms excluding PSP | The gate must not be the bottleneck |
| Intent Compiler p99 | < 5 ms | It is pure computation; if it is slow, something is wrong |
| Duplicate-charge rate | **0, alarmed** | The only truly unacceptable failure |
| `reconciliation_drift_total` | 0 | Non-zero means webhooks are being lost silently |
| `policy_rejection_total{reason}` | tracked, not minimised | Rejections are the product working |
| Webhook processing lag p95 | < 30 s | Beyond this, state is user-visibly stale |

`policy_rejection_total{reason=...}` is the interesting one and worth calling out in the
demo: a rejection is not an error, it is the system doing its job. Charting rejections *by
reason code* (which is why §1.6 exists) is how you would detect a compromised agent in
production — a sudden spike in `policy.tag_violation` from one `client_id` is an incident,
not noise.

### 5.2 Structured logs and one trace ID

Replace prose logging with JSON lines carrying a `trace_id` generated at ingress and threaded
through via `contextvars`, so the audit row, the Discord trace, the application log, and the
Razorpay `notes` field all carry the same ID. Then fix `audit.py` per §0.8 so the ID is
shared rather than re-minted per layer.

This is the difference between the demo claim ("correlated traces across three processes")
and the demo *proof* — searching one ID in the audit table and getting the complete story of
one purchase across every component. Being able to do that live, on a judge's arbitrary
choice of order, is worth more than any slide.

### 5.3 SQLite is fine — say why, and say what is next

SQLite in WAL mode is a reasonable choice here and you should defend it rather than
apologise: single-writer, one process, ACID, zero operational surface. But know the edges
and say them:

- One writer at a time; concurrent confirms serialise. At demo scale, irrelevant.
- `BEGIN IMMEDIATE` is required for read-modify-write on money (§0.8). The default deferred
  transaction gives you `SQLITE_BUSY` under contention, not correctness.
- The schema is deliberately Postgres-portable — no SQLite-specific types, JSON columns map
  to `jsonb`. The migration is Alembic plus a connection string.

"I chose SQLite, here are its three failure modes, here is the one-line path to Postgres" is
a better answer than "I used Postgres because it is production-grade."

### 5.4 Runbook

A one-page `docs/RUNBOOK.md` with four incidents — *duplicate charge suspected*, *webhooks
stopped arriving*, *a policy must be revoked immediately*, *the PSP is down* — each with
detection, the exact commands, and the rollback. Nobody expects a hackathon project to have a
runbook. That is precisely why it lands.

---

## Part 6 — The test strategy that proves all of this

Current tests: `test_mcp.py` (integration happy paths), `test_isolation.py` (AST walk over
`buyer_agent/` — genuinely good, keep it), `test_llm.py`. What is missing is everything that
would have caught Part 0.

### 6.1 Property-based tests on the compiler

`verify_cart_against_policy` is a pure function over small data. That makes it the ideal
target for Hypothesis, and the properties are the security claims themselves:

```python
@given(cart=carts(), policy=policies())
def test_compiler_never_approves_over_budget(cart, policy):
    ok, _ = verify_cart_against_policy(cart, policy, policy["merchant_id"], lookup)
    if ok:
        assert sum(i["unit_minor"] * i["qty"] for i in cart) <= policy["max_spend_minor"]

@given(cart=carts(), policy=policies())
def test_compiler_is_deterministic(cart, policy):
    assert run(cart, policy) == run(cart, policy)

@given(cart=carts(), policy=policies())
def test_adding_an_item_never_turns_reject_into_approve(cart, policy, extra):
    if not run(cart, policy)[0]:
        assert not run(cart + [extra], policy)[0]      # monotonicity
```

Monotonicity is the one to lead with in the demo. "Adding items to a rejected cart can never
make it acceptable" is a property, not a test case — and Hypothesis will hunt for a
counterexample across thousands of generated carts. *"I did not write test cases for the
security-critical function; I wrote its invariants and let a fuzzer attack them"* is a
sentence that lands with a senior engineer.

### 6.2 The adversarial suite

Each of these is a demo beat and a regression test. The first two would have caught §0.1
and §0.2.

| Test | Attack |
|---|---|
| `test_forged_policy_token_rejected` | `{"rawId": known_credential_id}` with no signature |
| `test_cart_swap_after_initiate_rejected` | initiate expensive → update cheap → confirm |
| `test_assertion_replay_across_checkouts` | reuse one assertion for a second checkout |
| `test_policy_hash_substitution` | valid signature, different `policy_json` |
| `test_expired_policy_rejected` | `expires_at` in the past |
| `test_wrong_merchant_rejected` | policy bound to `chai-co`, confirm at `gelateria-roma` |
| `test_cumulative_budget_exhausted` | N compliant carts that sum past `max_spend_total_minor` |
| `test_unauthenticated_credential_enrolment_rejected` | POST `/internal/webauthn/register` with no session |
| `test_concurrent_confirm_creates_one_order` | 10 threads, same idempotency key |
| `test_idempotency_key_reuse_different_cart_errors` | same key, different `checkout_id` → 422 |
| `test_webhook_out_of_order_ignored` | `payment.failed` after `payment_link.paid` |
| `test_webhook_bad_signature_rejected` | flipped byte in the HMAC |
| `test_sql_injection_in_sku` | `'; DROP TABLE cart;--` as a SKU |
| `test_prompt_injection_in_description` | poisoned catalog text → compiler still rejects |

The last one is the sharpest, and your plan already identifies it. Make it stronger by
demonstrating the injection *succeeding at the LLM layer* — the agent visibly builds the
attacker's cart — and *failing at the architecture layer*. The defence is not a better
prompt. The defence is that the LLM is not in the authorisation path at all.

### 6.3 Concurrency

```python
def test_concurrent_confirm_creates_exactly_one_order():
    with ThreadPoolExecutor(10) as ex:
        results = list(ex.map(lambda _: confirm(checkout_id, key="same"), range(10)))
    assert len([r for r in results if r.status_code == 200]) == 1
    assert session.exec(select(func.count()).select_from(Order)).one() == 1
```

This test fails against the current code. That is the point of writing it.

### 6.4 Ledger invariants

Generate random interleavings of RESERVE/CAPTURE/RELEASE/REFUND and assert the balance
identity holds and never goes negative, for every ordering. State machines are where money
goes missing, and the bug is always an ordering nobody thought of.

### 6.5 Golden vectors

Committed `(policy, sha256)` fixtures per §2.4, so a canonicalisation change breaks CI rather
than every previously signed policy.

---

## Part 7 — What I would actually build, in order

Given the freeze at end of Day 8, ranked by (credibility gained) ÷ (hours). The first tier is
not optional — it is the difference between a system that works and a system that *is what it
says it is*.

**P0 — correctness on the money path (~6h). Without these the central claim is false.**

1. Call `verify_assertion_policy` in `checkout_confirm` + `test_forged_policy_token_rejected` — 1h
2. Freeze the cart snapshot on the checkout; verify the compiler and the charge use the same
   object + `test_cart_swap_after_initiate_rejected` — 1.5h
3. Idempotency: unique constraint, request fingerprint, IN_FLIGHT state, 409/422 semantics — 1.5h
4. Enforce `checkout.expires_at`; move to timezone-aware datetimes — 0.5h
5. Authenticate `/internal/*` behind a merchant session — 1h
6. `BEGIN IMMEDIATE` on the confirm transaction — 0.5h

**P1 — the things that make a payments engineer nod (~8h).**

7. Reconciliation sweeper + `reconciliation_drift_total` — 2h
8. Ledger RESERVE/CAPTURE/RELEASE + release on failure/expiry — 2h
9. Cumulative policy budget keyed on `policy_hash` (§2.3) — 1.5h
10. Error taxonomy with stable codes (§1.6) — 1h
11. Webhook: real event ID from a captured payload, ordering guard, terminal states — 1.5h

**P2 — the things that win the room (~6h).**

12. **Step-up authentication above a threshold (§3.2)** — the best beat in the project — 2h
13. Pre-debit notification + policy revocation button — 1.5h
14. Hypothesis property tests on the compiler — 1h
15. Asymmetric tokens + a real `/oauth/jwks.json` — 1h
16. `docs/RUNBOOK.md` — 0.5h

Cut before any of the above: storefront CSS, the third A2A skill, the second merchant boot
(show the config diff instead — your plan already says this).

---

## Part 8 — The five sentences that win the room

A Razorpay engineer is not going to be impressed that the happy path works. They will be
impressed by these, in this order:

1. **"I reviewed my own money path and found that the WebAuthn assertion was verified during
   the browser ceremony but not at `checkout_confirm`. Here is the forged token that got
   through, here is the test that now fails against that commit, here is the fix."**
   Self-red-teaming is the rarest signal in a hackathon.

2. **"The compiler was validating the live cart while Razorpay was charging the frozen total.
   Same-object identity by hash is now enforced, and the test that proves it swaps the cart
   between initiate and confirm."** Shows you understand *why* mandates bind hashes.

3. **"Ten concurrent confirms with the same idempotency key produce exactly one Razorpay
   order."** Run it live. This is the single thing every payments engineer has been burned by.

4. **"Webhooks get lost. My reconciler polls Razorpay for orders stuck in non-terminal states
   and reports drift. It is zero right now — here is the counter."** Nobody in a hackathon
   builds reconciliation. It is the clearest signal that you have thought past the demo.

5. **"₹400 goes through with no human involvement. ₹4,000 stops and demands a fresh
   fingerprint on that exact cart. Risk-proportionate authentication — which is the direction
   Indian payments regulation has moved, and the reason the answer isn't 'always ask' or
   'never ask'."** Ties cryptography, product, and regulation into one live demonstration.

Close on the honest-limits slide. `plan.md` §7 already has the right instinct: stating what
your system is *not* is a scoring asset, and after the five sentences above it reads as
confidence rather than hedging.

---

## Part 9 — Honest limits (extending `plan.md` §7)

- **The signature authorises a policy, not a transaction.** Until §2.1's two-tier model ships,
  a stolen assertion is reusable for any compliant cart within the policy.
- **AFA at the merchant layer is not issuer AFA.** OpenStore authorises a *delegation*. The
  card transaction is still authenticated by the issuer under its own rules.
- **The compiler can only enforce what is in the catalog.** Tags come from merchant-controlled
  YAML. A merchant who mistags an item defeats the tag allowlist. Policies are only as good as
  the taxonomy underneath them — which is an argument for an independently attested product
  taxonomy, and an unsolved problem industry-wide.
- **No delegation chains.** Agent-of-agent breaks accountability; the OAuth `client_id` model
  has no notion of it. Open IETF/NIST work.
- **No agent directory.** Discovery is domain-hinted, the same bootstrap problem AP2 and ACP
  have.
- **Single-region, single-process, SQLite.** Correct for a demo, honestly stated, with the
  Postgres path described rather than pretended.
- **Test mode has no settlement.** Reconciliation runs against payment status, not settlement
  reports. Those answer different questions and the difference matters in production.
