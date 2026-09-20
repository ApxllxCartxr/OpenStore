# LOGS

Running record of changes and decisions for the OpenStore MVP build. Newest entry at the top. Every entry: what changed, why, and what it means for anyone executing `SPECS/PLAN.md`.

---

## 2026-09-21 · A7 + A8 — console, feed, logs, install

**411 tests pass**; ruff, mypy --strict and all four guardrails clean. Verified against the live stack through Caddy, not just in-process.

### The design system was imported, not approximated

`design/tokens.css` and eleven font files copied whole from `~/projects/portfolío/frontend` per §4, with `design/README.md` carrying the role rules **and the licensing flag**: two of the four faces ship no licence file, Open Sauce Sans is believed OFL but unverified, and PP Kyoto is a commercial Pangram Pangram face. Fine on this machine, a question the moment anything is recorded or hosted. Written down now rather than discovered later.

The console is the instrument-panel weighting: Iosevka throughout, tabular figures, hairline rules, `--bg-sunken` panels, **no animation**, `prefers-reduced-motion` honoured anyway. A test parses the stylesheet and asserts **no literal colour appears outside a token fallback**, so the dark theme is inherited rather than re-implemented.

### What the cuts actually mean in the UI

Attribution and the full health panel were cut up front to fund A6's four protocols. They are **absent rather than empty** — a tab rendering a blank panel looks broken, an absent one is a decision — and a test asserts `/agentic/attribution` 404s. Health keeps only the two things that could not be dropped: overdue holds and the dev-allowlist banner, both of which appear on **every** tab because the whole point is that somebody sees them.

### Two real bugs, both found by running it rather than reasoning about it

- **A route collision.** The console's `/agentic/{tab}` catch-all was registered before `/agentic/codes`, so the reason-code endpoint answered "no console tab 'codes'". FastAPI matches in registration order; the route now sits above the catch-all with a comment saying why, because the next person to add a console route will hit the same edge.
- **`design/` was not in the Docker image.** The console rendered correctly in tests and served an **unstyled page in the container** — `tokens.css` 404'd through Caddy. Exactly the class of thing that only shows up once it is deployed. The Dockerfile copies it now and the live check passes.

### The feed maps to the reader's enum, not ours

`low-stock` is our bucket and not Merchant Center's, so it folds into `in_stock` through a function with a test rather than a hopeful string. A feed carrying an invented value is rejected by the only reader that matters. Two rules hold without exception and both are asserted: only exposed items appear, and **no exact count ever does**.

### Logs redact by name, not by guess

A regex over values would miss `line1` and flag a SKU, so redaction is a deny list of key names applied at any depth. Transcripts are forensic; logs are operational — a Gate that refuses in 3ms and one that refuses in 3s after a slow Merchant look identical in a Transcript, which is why the log line carries the duration.

### The install gate

`openstore up <domain>` reads `.env.example` for its variable names rather than keeping a second list that would drift — a test asserts the two agree exactly. Every secret comes from `secrets`, the file is written `0600`, the dev allowlist is empty out of the box, and it **refuses to overwrite an existing `.env`**, because regenerating secrets over a live deployment orphans every signature it has made.

`scripts/` became a package so the install gate could be tested rather than only run.

---

## 2026-09-20 · A6 — four protocols, one core

**DONE WHEN met.** **379 tests pass**; ruff, mypy --strict and all four guardrails clean.

### The assertion this phase exists for

`test_the_core_transcript_is_byte_identical_across_all_four` runs the full flow — search → allow → cart → tap → fake-UPI → receipt → verify — four times through four envelopes and byte-compares the core Transcripts. Only `order_id` differs by construction and is normalised out. A companion test asserts the **envelopes actually differ**, because if they were all identical the byte-identity claim would be trivially true and prove nothing. A third asserts byte-identity holds **on the refusal path too**, which is the case where a translator drifting would matter most.

### The spec was vendored, not remembered

`vendor/acp/2026-04-17/openapi.agentic_checkout.yaml` is fetched from the repository and committed. A test parses it and asserts the four paths, the five `operationId`s and the seven top-level schemas §16.12 names are all present. That is what makes "a quarterly revision is noticed rather than silently drifted past" a build failure rather than a hope. The vendored document confirmed §16.12's pinned facts exactly.

### ACP: four operations work, one is refused

`completeCheckoutSession` refuses with `method-not-supported`, names the refused step, returns the approve URL, and **lists the credential fields it ignored** — so an integrator can see their Shared Payment Token was received and deliberately not used, rather than wondering if it was dropped. ADR-0008 and ADR-0013 at the envelope boundary, not an unimplemented gap.

Headers are per-operation: `getCheckoutSession` requires no `Idempotency-Key` because it mutates nothing, and inventing a requirement the spec does not have would be its own kind of non-conformance. A request with no `API-Version` is refused rather than defaulted — guessing against a quarterly spec is how a silent incompatibility ships.

### AP2 rides on UCP

It renders UCP's checkout because it *is* a layer over UCP, and the toggle reads `[MCP | UCP | UCP+AP2 | ACP]`. The `checkout_hash` comparison is the load-bearing check: without it a mandate proves only that *some* checkout was approved. Autonomous mode refuses `authority-kind-not-enabled` — it is the `mandate` kind, defined and refused in v1.

`alg: none` is refused at the door, and a test greps `ap2.py` to assert it does **no curve maths of its own** — there is one ES256 verifier in this codebase and a second would be a second thing to get wrong.

### Two bugs in my own replay, both instructive

- I gave each protocol a **different `agent_id`**, which made the Transcripts differ for a reason that had nothing to do with envelopes. The test was measuring my test harness.
- I called `orders.set-status` twice on **the same idempotency attempt**, so the second transition replayed the first result and the order sat at `confirmed` while the Ledger said `CAPTURE`. That is same-key-same-result working exactly as specified, and it is a trap worth naming: two distinct transitions need two keys.

### OPEN — A6

- **`fixed_order_salt_hex` added to the conformance fake**, so a golden replay produces comparable bytes — the same reason hour 0's `cart_hash` vectors pin a salt. Empty outside tests; a real Merchant always generates one.
- **`order_id_hint` added to door 7's request.** It lets a replay name its own order so Transcripts are comparable. A Merchant is free to ignore it and the real one will; it is documented as test-only at the call site.
- **Scope boundary held:** ACP and AP2 are request-path live and golden-replay proven, with no client integration for either, exactly as the phase text scoped. Only MCP has a live client, and that client is C's job.

---

## 2026-09-20 · A5 — evidence and verifier

**DONE WHEN met.** **337 tests pass**; ruff, mypy --strict and all four guardrails clean.

Five sections, hash-chained, ES256-signed, carrying their own JWKS snapshot. Exit codes 0/1/2 preserved from `main`, because a script piping receipts through the verifier needs to tell valid from tampered from untrusted without parsing prose.

### The verifier reports rather than summarises

A bundle never comes back "verified". It comes back with the Authority kind, the mechanism where there is one, and the Binding printed — and a `strength` line in the plan's own words. Under `upi-pin` that line says explicitly that the basket is bound *by reference through the Quote, not signed by the buyer*; under `passkey` it says buyer-attested over the exact basket. A COD `moved` section says the **Merchant asserts** the capture and no rail attested it.

**A claim-ordering bug, caught by its own test.** The COD branch fired on `merchant_asserted` before checking whether anything had moved, so a bundle sealed at `confirmed` with an empty `moved` section announced "the Merchant asserts this CAPTURE" about a capture that had not happened. Emptiness is checked first now. Precisely the flattering-claim failure the section exists to avoid, produced by my own code.

### Three details worth keeping

- **The chain link covers the section's name, not just its bytes.** Without that, two sections with identical payloads would be interchangeable and a `told` could be presented as a `moved`.
- **The JWKS snapshot is inside the signed payload.** A bundle whose key list could be swapped after signing would verify against whatever key an attacker supplied; a test swaps in a hostile keyring and expects `TAMPERED`.
- **Erased verifies, altered does not.** Without the salt both PII sections report `unopened` and the bundle is `VALID`, because erasure must never break verification. With the salt and an altered row it is `TAMPERED`. Those are different states and the verifier says which.

### COD sealing, and why it needed no new machinery

Sealed at `confirmed` with an empty `moved` section — every other section is already final — and collection appends an entry as v2 through **exactly the path a refund uses**. The same function serves both because from the bundle's point of view they are the same act: a new money event on an order whose other four sections were settled. The Consumer has a verifiable receipt from the moment they commit rather than only after they pay.

### `format_rupees`

Written first as `f"₹{minor / 100:.2f}"` with a money-lint marker, which was the wrong instinct: the marker exists for exact decimal arithmetic, not for making the lint quiet. This is the number a Consumer reads off a receipt, so it is `divmod` — integer arithmetic with no rounding mode to get wrong.

### The receipt viewer

`/receipt/<id>` is mounted **outside** the console's auth boundary. `/agentic` is session-authenticated for the Merchant, and a receipt that opens by unguessable id *with no login* cannot live there — putting it there would mean no Consumer could ever open their own. A test asserts the request carries no session. A missing receipt and a wrong id answer identically.

---

## 2026-09-20 · A4 — Authority, admission, order lifecycle

**299 tests pass**; ruff, mypy --strict and all four guardrails clean.

### Authority as data, not as an `if`

`authority/kinds.py` holds §6.5's table: each kind declares what it binds, by whom, and **when it lands**. That last part is why the table is data — a caller that could say "my authority landed early" could say it about `upi-pin`, and then a spend would pass check 1 on a ceremony that has not happened yet.

`mandate` is present with `live=False` rather than absent. Defined, registered and refused is a different claim from "we never thought about it", and a reader of the registry can see which one this is.

An OTP is refused **at the enum**: `IntentMechanism("otp")` raises. It cannot be configured, which is stronger than being validated away, and the reason is in the docstring — it proves control of a phone number, which is exactly what RTO fraud already defeats.

### The `consumer_id` gap the plan flagged, closed

A COD order authorized by the `passkey` mechanism never touches a payment rail, so there is no VPA to derive a pseudonym from. The derivation now takes a `HandleSource` (`payer-handle` or `credential-id`) and **folds it into the HMAC**, so the two sources cannot collide and a verifier can tell which was used. An empty handle raises rather than producing a null id — on exactly the path where attribution matters most.

### Tokens

A tap token is what makes "a hold cannot be created without spending an approve token" true. It is single-use, 5 minutes, bound to one `cart_hash`, and carries a `rendered_digest` of what the approve page actually showed — so a **Destination edit after render invalidates it even though the total did not move**. That is the gap hashes alone leave, closed on display rather than on hashes.

A resume token is session-bound and resolves both ids server-side. A stranger presenting someone else's token gets `not-found` and not "that belongs to another session" — the second answer is the oracle the token exists to close.

### The SSRF sink

Refusals happen **before any packet leaves the box**: scheme, then the metadata address, then resolve-and-check. The metadata check runs on what the host *resolves to*, not what it is called, and it is consulted before the allowlist, so no configuration can reach it.

§10.1's scheme carve-out is implemented and tested: `http://buyer-chat:3001` is admitted when named, and a *different* host in the same private range is refused on both dimensions — scheme first, address second. The allowlist is names, never a CIDR, because a range is not an exception.

### RFC 9421

The signature base is written once, here, with a Python signer alongside the verifier. A4c's real signer is the chat's in TypeScript; having both ends against one set of vectors before the chat exists is what stops the signature base being debugged twice by two people who each think the other side is wrong.

One design point worth keeping: **a failed verification does not burn the nonce.** Remembering a nonce from an unverified request would let anyone burn nonces they never signed, which is a denial of service dressed as replay protection. The test asserts the real request still succeeds afterwards.

### Lifecycle

One `expires_at` column, three deadlines, and the COD one **alerts rather than acting**: a parcel that is late is not a parcel that is lost, and only the Merchant knows which. The payment window is `min(15 min, provider link lifetime)` — a hold outliving the link it was taken for is stock nobody can pay for.

Transitions encode that `cancelled` is pre-money only and `refunded` post-money only, which is what keeps "status flip alone never moves money" true. An RTO is `cancelled` + `rto`, not a ninth status.

### OPEN — A4

- **The approve page and `/agent/*` routes are not wired yet.** A4's building blocks (tokens, admission, rate limits, lifecycle) are complete and tested as units; mounting them as HTTP routes lands with A5's console and A6's protocol surface, which is where the request path actually gets built. The DONE WHEN items that require an end-to-end HTTP flow — a stranger transacting with no prior Merchant action, an abandoned `confirmed` releasing at link expiry — are asserted at the unit level here and re-asserted end to end there.

---

## 2026-09-20 · A3 — Gate, Ledger, provider

**DONE WHEN met.** `uv run pytest -q` → **222 passed**; ruff, mypy --strict and all four guardrails clean. Every gate in the phase text has a test named after it.

### The Ledger

Single entry per money event, and `main`'s double-entry account legs deliberately not carried over: the invariants here are stated per order, and mixing the representations makes the arithmetic disagree without failing anything loudly.

**The `main` trap is closed and tested.** `create_refund_entry` keyed idempotency on the checkout id alone, so a second partial refund returned the first entry and wrote nothing. The key now carries the refund's own id, and `test_two_successive_partial_refunds_both_write` asserts two rows — the test that would have caught it doing nothing.

**A design bug of my own, found by the COD test.** `open_holds` was computed as `reserved − captured − released`, which made a COD capture look like it had closed a hold that never existed: the order read as "closed more than it held" and the invariant refused a correct cash sale. Whether an entry closes a hold is now **recorded on the row** rather than inferred from its kind. Escrow-zero then quantifies over `RESERVE` entries exactly as ADR-0018 says it should, and COD needs no exception at all.

Also: SQLite only autoincrements `INTEGER PRIMARY KEY`, never `BIGINT`, so the first version had every insert arrive with a NULL id and look like a duplicate-key collision. The column carries a `with_variant` now. And the duplicate-key path uses a SAVEPOINT rather than rolling back the session — a duplicate key is the *expected* outcome of a retry, and rolling back would discard every legitimate write before it in the same unit of work.

### The Gate

Twelve checks in §6.5's fixed order, byte-stable Transcript, dry-run with zero side effects. `decide()` / `settle()` / `collect()` are three entry points rather than two, and `collect()` is not a branch inside `settle()`: it verifies no Provider record because there is no rail, records nothing about Authority because `confirmed-intent` resolved days earlier at `decide()`, writes one `CAPTURE` with no preceding `RESERVE`, and runs no Gate check because the goods are already on the doorstep.

`upi-pin` **defers** at check 1 rather than passing, because the payer authenticates in their own PSP app and the Authority arrives with the money. Recording it as `pass` would put a false statement into signed evidence; `settle()` resolves it, and `assert_complete()` refuses to capture while anything is still deferred.

`quote-consistent` is implemented independently of the fake's §16.11 arithmetic, as A2 promised. It catches a tampered total, a tampered line fold, a quote that disagrees with the pinned Attestation, a non-zero `round_off`, and tax-inclusive lines that are not marked informational.

### Two things the tests pin that prose alone would not

- **A `sold-out` failure writes no `RELEASE`.** No hold was taken, so there is nothing to close, and the trait refuses one if attempted. An `amount-mismatch` arrives *after* a successful reserve and does release exactly its own hold — two causes, different ledger consequences, and they must not be written as one.
- **50 concurrent taps on 5 units yield exactly 5**, stock never negative, against the conformance fake. B2 repeats this against real Postgres; they test different things and both are required.

### Provider

Four methods, plus `block / capture-block / release-block` declared by nothing and consumed by nothing — the seam ADR-0024 wants, and explicitly not a Reserve Pay COD hold, which ADR-0018 retired. A test asserts no adapter declares it, so the seam cannot drift open unnoticed.

Webhooks are HMAC-on-raw-bytes with `event_id` dedupe. The raw-bytes point is tested by signing a body with irregular whitespace and showing that its own re-serialization fails verification — a signature checked against a round-trip verifies nothing.

Crash-mid-link adopts via `check-status`: the fake can crash after creating a link and before answering, and the recovery finds the orphan rather than creating a second link, which is how a Consumer gets charged twice.

### OPEN — A3 (ladder step 4)

- **`aiosqlite` added as a dependency.** §16.1 says pin at hour 0 and never upgrade mid-build; this is an addition rather than an upgrade, needed because the Ledger is async and SQLAlchemy's async engine needs an async driver for the test database. Postgres uses `psycopg`, already pinned.
- **`tags` (check 8) currently passes unconditionally.** It is separate from `blocked` (check 7) on purpose — blocked is "never", tags is the Merchant's own per-tag refusal list — but no Policy field feeds it yet. It needs the `/agentic` console (A5) to have anything to enforce, and it stays in the check order because removing it would change Transcript bytes.

---

## 2026-09-20 · A2 — trait client and conformance fake

**DONE WHEN met.** Conformance suite green: retry-safety, external-sale visibility, byte-for-byte quote determinism, a flat-price Merchant's zero-value lines passing unchanged, and `variant-required` on a group id at every door taking a SKU. `uv run pytest -q` → **159 passed**, everything else clean.

### The fake is a Merchant, not a mock

It is an ASGI app and the client reaches it over real HTTP through `httpx.ASGITransport`, so every test exercises HMAC verification, idempotency replay and reason-code → status mapping rather than stepping around them. It also misbehaves on purpose, because that is what the Gate is for: `quote_drift_paise` moves the price between calls so `quote-fresh` has something real to catch, and `external_sale()` lowers stock outside the agent path so "the next Gate re-reads fresh" is a test rather than a claim.

**§16.11's arithmetic is implemented in the fake, deliberately, and A3 will re-implement the check independently.** If the Gate and the Merchant shared a pricing module, `quote-consistent` would prove only that the sidecar agrees with itself. The fake computes the §16.11 worked example from its own seed and lands on 249800 / 9900 / 15827 / 11894 / 11894 / 259700 — the same numbers hour 0's golden vector pins, reached by a different route.

### The client is async, and that was a design decision rather than a test convenience

`httpx.ASGITransport` is async-only, which forced the question early. The answer would have been the same regardless: a synchronous HTTP call inside a FastAPI handler blocks the event loop for the length of the Merchant's round trip, and the Gate makes several per decision (`quote`, `reserve`, `orders.set-status`). Under any concurrency that is the sidecar serialising itself behind the slowest Merchant response. Better found at A2 than at A3 with the Gate written against a blocking client.

### Three decisions worth the ink

**The signature covers the path, not only the body.** `release` and `commit` both carry `{order_id}` and nothing else, so a signature over the body alone makes a signed release a signed commit. An attacker who can redirect a request gets a free close of somebody's hold.

**`reserve` checks every line before it moves any stock.** Decrementing as it goes leaves a basket half-reserved when line three is sold out, and the caller then holds stock it has no order to release.

**A refusal replays as a refusal.** Same idempotency key, same result, including the error — the Merchant caches the refusal too. A retry that succeeds where the first attempt refused is a second hold with extra steps, and the test plants a restock between the two attempts to prove it.

### Two guardrail findings from A2's own code

- The money lint flagged `timeout: float = 10.0` in the client. A timeout is a duration, not money — but rather than teach the lint an exception, the field is now `int` seconds. "No float anywhere in the sidecar" is enforceable; "no float except where it is fine" is not.
- `ruff format` wrapped the tax extraction and moved `# money-lint: decimal` onto the closing-paren line, so the line-anchored marker missed and the lint failed on correct code. The marker now matches anywhere in the **enclosing statement's** line span. A guardrail that fails whenever the formatter rewraps is a guardrail somebody deletes.

---

## 2026-09-20 · A1 — skeleton and guardrail harness

**DONE WHEN met.** App boots behind the proxy split; the harness goes red on planted violations and green on the tree. `uv run pytest -q` → **105 passed**. `ruff`, `mypy --strict`, firewall, money lint, time lint, `CODES.md` diff and the prose scan all clean.

### The gate, run rather than asserted

`docker compose up -d --build` → caddy, sidecar and postgres healthy. Against the live stack:

- `curl -H 'Host: spoiledduckie.localhost' http://127.0.0.1/agentic/codes` → the reason-code set, served by the sidecar through Caddy on the Merchant domain.
- `/readyz` → `ready`, and it carries the `dev-profile-allowlist-active` warning, because compose sets `OPENSTORE_DEV_PROFILE_HOSTS=buyer-chat:3001` and SPEC §14 requires the exception to be visible in health output.
- `GET /` → **502**, which is the correct answer and the point of the check: `/` belongs to the store, the store is not built yet, and a 200 would mean the split had leaked. `chat.localhost` likewise — the site block exists per §10 so the third surface has somewhere to arrive.

### What landed

- `app.py` (FastAPI, `/healthz`, `/readyz`, `/agentic/codes`), `core/settings.py`, `Dockerfile` (non-root — the container holds the signing key), `Caddyfile` with both site blocks, `docker-compose.yml`, `docker/postgres-init.sql` (two databases, two roles, and an explicit `REVOKE CONNECT ... FROM PUBLIC`, since PUBLIC gets CONNECT on every new database by default and "no cross-grant" would otherwise quietly mean "can connect and read the public schema").
- `scripts/lint_money.py`, `scripts/lint_time.py`, `scripts/lint_firewall.py`, and `.github/workflows/ci.yml` wiring all of them plus hour 0's `registry_diff` into every push.
- `tests/test_harness_planted_violations.py` — 18 cases. Every lint is run against a file written to fail it *and* against the real tree, because a guardrail nobody has watched fail is a guardrail nobody knows works.

### Two decisions worth the ink

**Money lint bans `/` outright, with an escape.** `int / int` silently yields a float and AST cannot see types, so true division is refused and the tax extraction (§16.11 step 5) carries `# money-lint: decimal`. The marker is not a weakening: it makes every place in the system where money is divided greppable, which is a list worth being able to read. Also refused: float literals, `float()`, and `round()` — banker's rounding disagrees with §16.11's ROUND_HALF_UP on exactly the .5 cases money hits.

**`extra="forbid"` on Settings was wrong and is now `ignore`.** Written as a typo guard, it was caught by its own test suite reading the repo's real `.env`. The compose demo shares one `.env` across three services (§10), so a variable the sidecar does not declare is usually the chat's — and forbidding it would mean the sidecar dies at boot because another surface added a variable. Typo detection did not go away; it moved to `test_env_example_and_settings_agree`, which diffs `.env.example` against the fields **in both directions**. A field with no template entry is one an operator cannot set; a template entry with no field is a typo that reads as an empty secret. CI is the right place for that, boot is not.

### Note for A4c

§10.1's scheme carve-out is now in `.env.example` and in the `Settings` field description: the dev allowlist permits plain `http` for its named entries, because the hardened fetcher is HTTPS-only and public-IP-only, and both of those refuse `http://buyer-chat:3001`. Without it the demo's own self-registration is refused by its own hardening.

---

## 2026-09-20 · Hour 0 — contracts frozen

All seven steps of §12's hour-0 checklist, in order. `uv run pytest -q` → **74 passed**; `ruff check`, `ruff format --check` and `mypy --strict` clean.

### What landed

1. **Skeleton.** `src/openstore/sidecar/{core,trait,gate,ledger,authority,admission,provider,evidence,protocols,console,verify}/`, plus `tests/`, `scripts/`, `design/`, `lists/`. `pyproject.toml` adapted from `main`: dropped `discord.py`, `langgraph`, `typer`/`click` and `rich` (no surface in the rebuild uses them), dropped `[project.scripts]` (an entry point naming a module that does not exist is a broken install — `openstore up` arrives with A7), dropped `main`'s `[tool.mutmut]` block (it lists money-path modules that do not exist yet). Added `jsonschema` for step 5. `uv.lock` committed; nothing upgrades mid-build.
2. **`core/codes.py`** — 17 closed sets, 32 reason codes, and the reason-code → HTTP-status table. §6.4 listed eleven sets; the other six were named in prose and registered nowhere, which is precisely the drift the registry exists to stop.
3. **`scripts/registry_diff.py`** — generates `docs/CODES.md` from the enums, `--check` diffs it. Direction reversed from `main`, which diffed source against a hand-maintained `REGISTRY.json`: there is no hand-maintained file left to drift. `--prose` additionally scans the normative files for backticked identifiers in no closed set.
4. **`tests/GOLDEN/cart_hash/vectors.json`** — three vectors, hashes pinned. `mixed-basket` reproduces §16.11's worked example to the paise (shipping 3955/5945, IGST 15827, CGST/SGST 11894/11894, total 259700) and the test recomputes it rather than trusting the literal.
5. **Door and Quote JSON Schemas** in `trait/schema/`. Shapes only.
6. **`.env.example`** per §16.1, every variable named, no values.
7. **The D1 amendment** — Next.js → SvelteKit in `SPEC.md §1`, `PLAN-merchant-site.md` and `PLAN.md`.

### Added to §6.4 under standing rule 1 (registry and spec in the same commit)

- **`cancel-order` + `cancel-not-allowed`** — closes `docs/REVIEW-findings.md` R4, decided by the operator before step 2 rather than laddered. Scope `start-checkout`, own orders only, pre-money only, `RELEASE` where a hold exists, refusing at or past `paid`. Without it an agent that built a basket could only abandon it, holding a Quote for 24h and held stock until the payment window lapsed — an inventory-denial path open to any self-registered stranger, which is the same reasoning that time-boxed `confirmed` in the first place.
- **`dispatch-not-allowed`** — ADR-0020 bounds dispatch to `confirmed` onward.
- **`untrusted-key`** — PLAN-sidecar S8's DONE WHEN already refused a post-revocation signature with it, and it was in no set. Found by the prose scanner on its first run, which is the whole argument for having one.
- **Six unregistered closed sets** promoted out of prose: trait doors, gate checks, provider operations, availability buckets, ceremony, discount visibility, protocols, tool names. The gate-check names are written into the Transcript, so they were frozen bytes living in a bulleted list.

### OPEN — h0 (ladder step 4)

- **Destination and Contact field shapes.** §16.5 pins the two demo addresses as prose and never names the object. Frozen as `{line1, line2?, city, state, postal_code, country}` and `{email?, phone?}` — these feed `destination_hash` / `contact_hash`, so they are preimage bytes now. Reversible only by regenerating the vectors, which means before A3.
- **Scopes for `order-status`, `cancel-order`, `request-refund`.** Both plans group them as "reads, and scoped" without naming a scope. Conservative reading taken: the read takes `search`, the two that change an order's fate take `start-checkout`.
- **Canonical PII bytes.** §6.3 says "canonical PII bytes" without defining them. Taken as the same canonical JSON as everything else (sorted keys, no whitespace, UTF-8 unescaped) so there is one canonicalization in the system rather than two.

### Two prose fixes the tooling forced

- `set-status` → `orders.set-status` across SPEC §5/§6 and two plans. It was shorthand for door 8, and shorthand is how a closed set stops being closed.
- `F12` (tax-invoice series) marked **half closed** in `REVIEW-findings.md`: ADR-0020 supplies the series, credit notes for partial refunds remain open and still need counsel.

---

## 2026-09-20 · Verification pass on the fetched specs — all claims hold, four refinements

The step-0 findings were committed on the strength of page summaries. Re-fetched against raw sources asking for **verbatim quotes** rather than paraphrase, because those claims are now pinned authority that translators will be built from.

### Verified verbatim — every load-bearing claim holds

AP2, quoted from `docs/ap2/specification.md`:

> "AP2 operates as a security feature within a Commerce Protocol."
> "AP2 is designed explicitly to be compatible with the Universal Commerce Protocol (UCP) and integrates seamlessly."
> "AP2 defines two Mandate types: Checkout Mandate and Payment Mandate."
> "Verify that the hash of the Checkout JWT sent for approval matches the value included for the `checkout_hash` claim."
> "The `vct` value includes a numeric suffix that acts as a schema version number (e.g. `mandate.payment.1`, `mandate.checkout.open.1`)."
> "The Trusted Surface role is a UI surface that is trusted to get informed user consent for an Intent before creating a user-signed Mandate."
> "Human Present (Direct): The User directly sees the closed Checkout and approves it and its payment explicitly."
> "Human Not Present (Autonomous): The User sees and approves a set of constraints over what closed Checkout and Payment would meet their intent."

**So the AP2-as-a-layer decision is not an interpretation — it is the specification's own sentence**, and it is now quoted in §16.12 so it can be defended in the room rather than asserted.

ACP, from the raw `openapi.agentic_checkout.yaml`: all four path keys, all five operationIds and the header sets confirmed as recorded.

### Four refinements

1. **"Five endpoints" was loose.** It is **four paths, five operations** — `/checkout_sessions/{checkout_session_id}` carries both POST (`updateCheckoutSession`) and GET (`getCheckoutSession`). Corrected everywhere, because a pinned reference that miscounts its own surface invites a wrong implementation.
2. **Headers are per-operation, not blanket.** `Authorization` and `API-Version` on all five; `Idempotency-Key` on the four POSTs only — **`getCheckoutSession` has none**; `Content-Type` only where there is a body. The plan had listed all four as universally required.
3. **Authorship conflicts between sources and is now recorded as such.** The repository says the spec is *maintained by OpenAI and Stripe*; Stripe's documentation says *created by Stripe, OpenAI and Meta*. The plan previously asserted the three-party version flatly. Guidance added: **say "OpenAI and Stripe" on stage** and you cannot be contradicted by the repo.
4. **ACP revises quarterly, which the plan had not accounted for.** `spec/` holds `2025-09-29`, `2025-12-12`, `2026-01-16`, `2026-01-30`, `2026-04-17` and `unreleased` — five dated releases in under a year. Two consequences now written in: **pin `2026-04-17`, never build against `unreleased`**, and **the conformance badge must name the spec version it targets** (`ACP 2026-04-17`), because an unqualified "ACP conformant" ages badly against a spec that moves every quarter. A6's DONE WHEN now asserts the vendored OpenAPI matches that version, so a revision is noticed rather than drifted past.

Point 4 is the one with legs beyond this build: the badge already names payment-instrument and completion deviations, and spec version belongs in the same honest sentence.

---

## 2026-09-20 · A6 STEP 0 DONE — specs fetched, and AP2 was not what we assumed

Pulled the ACP and AP2 specifications before hour 0 rather than at hour 17.5. **It immediately overturned two things this plan had asserted from memory.** This is the step-0 gate working exactly as intended, one day early and at zero cost.

### Versions pinned (the §0 requirement)

| Protocol | Source | Version read | Licence |
|---|---|---|---|
| ACP | `github.com/agentic-commerce-protocol/agentic-commerce-protocol`, `agenticcommerce.dev` | spec **`2026-04-17`** | Apache 2.0 |
| AP2 | `github.com/google-agentic-commerce/AP2`, `docs/ap2/specification.md` | `main` @ 2026-09-20 | open, Google-led |

### Correction 1 — AP2 is a security layer, not an envelope

The plan had AP2 as a fourth sibling translator beside MCP, UCP and ACP. The specification says otherwise, plainly: **"AP2 operates as a security feature within a Commerce Protocol"**, catalogue and checkout APIs are **outside its scope**, and it is **"designed explicitly to be compatible with the Universal Commerce Protocol (UCP)."**

So `ap2.py` is now a **mandate layer over `ucp.py`**, and the header toggle reads `[MCP | UCP | UCP+AP2 | ACP]`. A fourth peer tab would have been a nicer-looking lie. Building it as a sibling would have meant discovering at hour 19 that we had written a standalone envelope for a thing that explicitly is not one.

### Correction 2 — two mandates, not three

The plan said Intent / Cart / Payment, which is the September 2025 launch-announcement description. The current spec defines **two**: **Checkout Mandate** (`vct` e.g. `mandate.checkout.open.1`, carrying a `checkout_hash` claim) and **Payment Mandate** (`vct` e.g. `mandate.payment.1`, carrying `checkout_hash`, `transaction_id`, and a `cnf` claim holding the agent public key in autonomous mode). Both are JWTs bound to a merchant-signed Checkout JWT.

Direct (human-present) is the mode we support — the agent presents mandates to a **Trusted Surface** for the user to review and sign, which is our approve page. Autonomous (human-not-present) pre-approves constraints, which is our `mandate` Authority kind: registered, recorded, refused in v1.

### ACP fits better than budgeted

Maintained by **Stripe, OpenAI and Meta** — the plan said OpenAI + Stripe and was out of date. Five checkout-session endpoints, all mapping close to 1:1 onto our core, and crucially it ships **machine-readable OpenAPI and JSON Schema**, so validation is generated rather than hand-transcribed.

`POST /checkout_sessions/{id}/complete` is the single refusal point — it carries the delegated credential (Stripe Shared Payment Token, or a `vt_…` vault token under OpenAI's Delegate Payment spec). The other four operations work normally. Two pleasant surprises: ACP has **native `Idempotency-Key` headers** mapping straight onto our per-attempt discipline, and its *Delegate authentication* building block is **OAuth 2.0**, which is already our allowlisted-agent admission route from ADR-0012.

### The finding worth putting on a slide

AP2's `checkout_hash` — a signed mandate bound to the hash of a merchant-signed Checkout JWT — is **structurally the same primitive as our `cart_hash`**, a signed Authority bound to a merchant-signed Quote (§6.3). We arrived at it independently, and their Trusted Surface is our approve page.

Written into the plan as "structurally the same primitive," deliberately not "identical" — the field sets differ and the claim has to survive someone opening both specs in front of you.

### Budget unchanged

A6 stays at 4h. ACP's published JSON Schema pays for the five endpoints; AP2-as-a-layer is cheaper than AP2-as-an-envelope would have been, and it reuses A4/A4c's ES256 verification rather than adding a second JWT verifier. Track P remains at −1.0h.

---

## 2026-09-20 · D3 REVISED — all four protocols live (ACP and AP2 promoted)

Operator asked for the ACP and AP2 translators. D3 previously shipped them declared-and-refused. They are now real translators alongside MCP and UCP.

### Why this is worth real hours

Two live tabs plus two greyed ones makes the "one core, many envelopes" claim at half strength. More to the point, **the deviations are the argument rather than an embarrassment**: each of these protocols wants something at the completion step that this product refuses on purpose, and the badge now names it per protocol.

- **ACP** is built around a delegated payment credential handed to the agent — exactly the authority ADR-0008 and ADR-0013 removed. The translator accepts the whole flow and refuses that one step with a named code, returning the approve URL. Refused on purpose, not unimplemented.
- **AP2** is mandate-based. Human-present maps cleanly onto the approve ceremony and the `cart_hash` binding; human-not-present rests on a pre-signed intent mandate, which is the `mandate` Authority kind — registered, recorded, refused in v1 (ADR-0017).

Two protocols where the refusal *is* the product beat beats two greyed tabs, and the conformance badge now does real work across four envelopes. New demo beat 7b at 60s, flagged as the architecture moment.

### Cost, and the correction to my own number

A6 2h → 4h. Funded by taking **cut #2 up front** (`/agentic` Attribution and Health tabs, −1.0) and making **Exposure read-only** over seeded values (−0.5). A7 drops 2.5h → 1.0h.

I first wrote this as "net −0.5h, unchanged." **That was wrong.** +2.0 against −1.5 is net +0.5, so **Track P moves from −0.5h to −1.0h** against the day. Corrected in the arithmetic table and in D3 rather than smoothed over. Track S is untouched at −0.5h.

Where the hour comes from: the rehearsal block compresses 1.5h → 0.5h, and A6 carries an automatic escape (below) that returns 2h without anyone deciding. Noted explicitly that **the rehearsal is not the buffer** — if Track P is behind at Integration 3, take cut #1 and reverse ACP/AP2 rather than skipping the rehearsal.

### The prerequisite that makes this safe

ACP and AP2 are young specifications and **nobody on this build has their wire formats memorised, including whoever wrote this plan.** A translator built from memory is a false conformance claim, which is the one failure this product cannot survive — the whole pitch rests on the badge being honest.

So A6 now opens with a blocking **step 0**: fetch the published specs, record URL and version in `LOGS.md`, map against the document. §16.12 repeats it and states the boundary precisely — the *shape* of each deviation (ACP's delegated credential, AP2's two modes) is safe to rely on from this plan; **every field name, envelope key and endpoint path comes from the fetched document.**

**If a spec cannot be retrieved, that protocol ships declared-and-refused with the badge saying exactly why, and A6 reclaims its time.** That is a designed escape, not a failure, and it is what makes this addition self-limiting rather than an open-ended risk on the track with no margin.

### Cut list renumbered

Cut #1 is now "ACP and AP2 back to declared-and-refused" (−2h, a clean reversal), with #1b "UCP too" (−1h more) only if A6 is genuinely drowning. Cut #2 is spent. Cuts 4a and 5 remain spent from the Option B decision.

### Also changed

`protocols/registry.py` is now one place naming all four protocols, their live/refused capabilities and their deviation text, with the header toggle and the badge both reading from it — so a protocol cannot be live in one and stale in the other. A6's DONE WHEN requires a golden replay per protocol with **the core Transcript byte-identical across all four**, which is the single most valuable assertion in the phase. Day 2 schedule rebuilt: A6 occupies 17.5–21, Integration 2 moves to 21–21.5.

---

## 2026-09-20 · Pre-hour-0 validation — four defects found and fixed

Last pass before the build starts. Cross-checked §16 against §6, §8 and §13, and checked whether hour 0 is executable as written. Four things were wrong.

### 1 — The item count was wrong, and the way it was wrong was dangerous

Claimed "exactly 16 Catalogue Items." The §8 table sums to **15** (tote 3 + cap 2 + ten singles), and §16.3 has 16 *rows* because one of them is `SD-TOTE-BLK-L`, the deliberately-absent combination.

An agent building the seeder from a 16-row table told to produce 16 items would have seeded `SD-TOTE-BLK-L` — with zero stock or a null price — and broken **two** gates at once: "seed loads with zero null-stock rows" and "the unavailable combination renders as unavailable." The whole point of that row is that it does not exist.

Corrected to **15** everywhere. The row is now struck through and labelled **DO NOT SEED**, with a sentence above the table saying it has 16 rows of which you seed 15, and why. Also fixed a broken markdown row in §8 that had swallowed the following paragraph into a table cell.

### 2 — `core/codes.py` was assigned to two different hours

Hour 0 said to commit it; A1 said to write it. Worse, hour 0 would have been writing into `src/openstore/sidecar/core/` before A1 created that layout.

Hour 0 is now an **ordered seven-step checklist** where each step needs the one before: repo skeleton and `pyproject` first, then `codes.py` (**here, explicitly not in A1**), then `registry_diff.py` and the generated `docs/CODES.md`, then the `cart_hash` golden vectors, then the door and Quote JSON Schemas, then `.env.example`, then the D1 amendment. A1 now *wires codes.py into CI* rather than creating it.

Added: **Track S does not wait for hour 0.** The `design/` import and app scaffolds depend on none of it, so an overrunning hour 0 does not idle the track that has no margin.

### 3 — Inclusive-price arithmetic was unpinned, and it is the one place the money core eats itself

§16 said prices are tax-inclusive but never said whether **shipping and discounts** are, never gave the inclusive-tax extraction formula, and — the real trap — never said how to split an **odd-paise tax into CGST and SGST**.

The Merchant computes the Quote (B2) and the Gate re-checks it (A3). If they round differently by one paise, `quote-consistent` fires on a *correct* quote, and that presents as a bug in the money core rather than as a missing paragraph here. It would have been a genuinely miserable thing to debug at 3am.

New **§16.11** pins the whole order of operations: fold Add-ons, apply discounts to inclusive amounts, apportion shipping by line inclusive total, largest-remainder with ties broken by SKU ascending, extract tax as `ROUND_HALF_UP(inclusive × rate_bp / (10000 + rate_bp))` in exact decimal, and split odd paise as `CGST = tax // 2, SGST = tax − CGST` — **SGST takes the odd paise**, stated once, never re-decided. `round_off_minor` is always `0` in v1, which removes a whole class of ambiguity.

It closes with a **worked example**, computed and verified rather than illustrative: the demo basket to destination B gives subtotal 249800, shipping apportioned 3955 / 5945 (the leftover paise going to the larger fraction), IGST 15827 on the tote line, CGST 11894 / SGST 11894 on the service line, **total 259700 = ₹2,597.00**, with both GST splits in one Quote. That is now the first fixture, the Integration-1 expected output, and the demo's beat-4 number.

### 4 — Shipping and discount inclusivity, marked where the values live

`incl. GST` now appears on both shipping rows and `off the inclusive total` on both discount codes, because a value read in §16.5 is read without §16.11 open beside it.

### Verified correct, not assumed

- Demo basket clears every seeded policy limit: ₹2,597 against a ₹25,000 cap, 3 lines against a 10-line limit.
- `SD-PLUSH-MINI` cap of 2 matches the §13 eighth-minute refusal beat; stock 10 supports it.
- `SD-RECALLED` carries the `recalled` tag and §16.4 blocks that tag — the blocked check has something to refuse.
- Both demo destinations avoid the `19xxxx` unserviceable range; 560038 matches the KA zone, 400028 the rest-of-India zone.
- `SD-CHARMBAR-SEAT` at POS Karnataka against a Karnataka-registered Merchant yields CGST/SGST while the tote to Maharashtra yields IGST — the two-split basket works as claimed.
- Three distinct GST rates (3 / 12 / 18%) are present in the seed, so apportionment is exercised rather than trivially correct.

---

## 2026-09-20 · Unattended-run hardening — §0 rewritten, §16 added

Requirement: the build runs overnight with nobody awake, so **no hour may contain a question**. Two problems, both fixed.

### Problem 1 — §0 literally instructed the executor to stop and ask

The old protocol said "stop and ask" under rule 1 and "3. Ask." under *When blocked*. That is exactly the 3am wake-up, written into the plan as policy.

§0 now opens with **"never ask, decide by the ladder"**, a four-step resolution order that always terminates in action:

1. §16 Pinned values → 2. `SPEC.md` + its ADR → 3. §6 + phase text → 4. **take the most conservative option that keeps the gates reachable, log it under `## OPEN — <phase>`, continue.**

Conservative is defined rather than left to taste: fail loud over coerce, refuse over allow, narrower over wider, existing identifier over new one.

**"Blocked" is now narrowed to one case only** — a gate that cannot go green without changing a frozen §6 contract. Everything else is a decision, not a block. Even then the instruction is: log it, build the rest of the phase, commit, **move to the next phase**. Never wait.

Rule 1 also softened in the right direction: adding a genuinely missing code **to the registry and §6 in one commit** is now explicitly allowed and logged, because the old wording ("stop and ask") turned a five-minute registry edit into a stalled track. Inventing one at a call site is still a red build.

Added alongside: **overrun rule** (a phase more than 50% over budget takes the next §12 cut rather than grinding), **commit at every phase boundary even if cut short** (a run that dies at hour 19 should leave 18 committed hours, not an uncommitted tree), and a **morning report** spec for `LOGS.md` — phases with gate output pasted, every `## OPEN —`, every `## BLOCKED —`, every cut with its hour. Gate output is now required every time, not "if in doubt": an unattended run's claim that something passed is worth exactly what the pasted output under it is worth.

### Problem 2 — the real blocker was unpinned values, not the ask rule

Sweeping for open decisions turned up the larger issue: **the seed named 16 items and priced none of them.** No GST rates, no HSN codes, no stock counts, no thresholds, no GSTIN, no registered state, no addresses, no discount codes, no rate-limit numbers, no COD delivery window, no DB names or roles, no admin credentials, no scripted-driver transcript. An agent at hour 2.5 would have invented all of it — and then the golden vectors, the two-GST-split test and the demo script would all have rested on values nobody chose.

New **§16 Pinned values**, 11 subsections: infrastructure and ports, merchant tax identity, the full 16-item catalogue with prices/HSN/GST/stock/thresholds, seeded policy, shipping zones and the two demo destinations, discount codes, every timing, every rate limit, the `scripted` driver's exact tool-call sequence, fake-provider behaviour, and an explicit list of what is deliberately *not* pinned (copy, images, component structure).

Choices worth recording:

- **Three GST rates** (3% / 12% / 18%) rather than one, so apportionment and largest-remainder rounding are actually exercised instead of trivially correct.
- **Karnataka (29) as the registered state**, destinations Bengaluru 560038 (intra) and Mumbai 400028 (inter). `SD-CHARMBAR-SEAT` is a service fixed at Place of Supply Karnataka regardless of destination — that single row is what makes a two-place-of-supply basket possible, and both the Integration-1 smoke test and demo beat 4 depend on it.
- **Tax-inclusive pricing** (Indian MRP), so tax lines are `informational: true` and must not be added again. The inverted case is still seeded and tested, because getting it backwards double-charges every order.
- **GSTIN `29AABCS1429B1ZQ` is fabricated** — correct in shape, belonging to nobody, and marked as such in place so it is never presented as real.
- **No invented version numbers.** Dependencies are resolved by `pnpm add` / `uv add` at hour 0 and pinned by committed lockfiles. This covers `http-message-signatures` and the B3 table primitive.
- All 128-bit values come from `secrets.token_bytes(16)` / `crypto.randomBytes(16)` — stated because "unguessable" without a named source is how a demo ends up seeding from a timestamp.

Cross-references added at every point where a value was previously left open: B1's seed points at §16.3 as authoritative, A7's console tabs name their seeded values, A4's rate limits point at §16.8, A1's `.env.example` at §16.1, D7 at §16.9, and the demo script and Integration-1 command now name the exact SKUs, destinations and codes they use.

---

## 2026-09-20 · CONFIGURATION DECIDED — Option B, two builders, no third

The §12 hour-0 decision is made and closed. **Option B: two builders, cuts 5 and 4a in force from hour 0.** Anyone or anything executing the plan takes this as given and does not re-open it.

### What is now out of scope

- **Cut 5 — no direct storefront cart or checkout.** The store browses and hands off to an agent; it never creates an order itself. The `site_carts` table and its 30-minute-hold sweeper are **not built** — they existed only to hold stock for that cart. A **Buy via agent** panel carrying the sidecar card URL replaces it on every group page, and that is the surface the demo actually uses.
- **Cut 4a — admin trimmed, not gutted.** Out: CSV export, bulk archive, and the pricing-setup CRUD. Shipping zones, GST identity and discount codes are **seeded in B1 and changed by re-seeding**. In, unchanged: dashboard, catalogue create/edit, variant matrix, stock, price, inventory moves, orders + Timeline, dispatch + invoice, refund dialog, Record collection, Record RTO.

The line held deliberately: everything on the *operating* path stays, only the *setup-once* screens go, so Surface 2's claim — "a Merchant can operate it" — survives intact. The blunter read-only-admin cut would have bought the same ninety minutes and taken that claim with it.

### Budgets after the decision

| | before | after |
|---|---|---|
| B1 schema + seed + storefront | 6.0h | **4.5h** |
| B3 shop-ops admin | 3.0h | **1.5h** |
| Track S phase subtotal | 26.5h | **23.5h** |
| Track S total (incl. 3 integration windows + polish) | 29.5h | **26.5h** vs 26.0 available |

Both tracks now sit at 26.5h against 26.0. The −0.5h is absorbed by the polish pass, budgeted 1.5h and scheduled 0.5h.

### The change that mattered most for an agent-executed build

Recording the decision in §12 alone would not have been enough. The phase bodies in §8 still described **full** scope, so an agent building B1 would have built the direct checkout and an agent building B3 would have built the pricing CRUD — the schedule says one thing, the instructions another, and the instructions are what gets read at the point of work.

So both cuts are now stated **as blockquote banners at the top of B1 and B3**, the removed scope is struck through in place with its full text kept for a later phase, and the DONE WHEN gates are rewritten to match what is actually being built. §1's deliverable and §2's D9 trim list were corrected to match.

One contradiction surfaced while doing this and is fixed: B1's new gate first read "no route anywhere in the store creates an order," which would have forbidden **door 7 `orders.create`** — implemented by the store in B2 and called by the sidecar over the private network. Corrected to "no **public storefront** route creates an order; door 7 is the only path that does."

### Option A kept on the record

Option A (a third builder takes Surface C — 11.5h, lifting out cleanly since B and C share no state, code or database) is retained in §12 as the path **not** taken, so that if a third pair of hands appears mid-build the restoration is mechanical rather than a redesign. Under it, cuts 5 and 4a would both be restored.

---

## 2026-09-20 · Track S re-budget — three changes applied

Prompted by a question about Track S, which turned into a second look at the schedule. Audit #1 fixed the schedule's *arithmetic*; this fixes its *estimates*, which were a separate and larger error.

### The finding behind it: the reuse asymmetry

Checked and confirmed: **`main` has no `package.json` and no `node_modules`.** Its entire UI is Jinja templates plus three vanilla JS files served from FastAPI. So Track P inherits ~7,000 lines of debugged Python *and* can crib `/agentic` from main's existing studio templates, while **Track S inherits nothing from `main` at all** — both SvelteKit apps are greenfield, and its only reuse is the portfolio design system.

Equal hours in the table, unequal risk in reality. Track S also gates all three integration points. Neither fact was written down; both are now.

### Two phases were under-budgeted — correcting my own numbers

- **C1 was 3h and could not have been.** It contained the whole chat UI *plus* ES256 key generation, Agent Profile publishing, RFC 9421 request signing, a three-driver model seam, the scoped action set and a bounded tool loop. Realistically ~5h.
- **C3 was 3h and was light.** Eleven distinct UI states, a verbatim signed-quote renderer with a byte-equality assertion, a pre-display signature check, and named copy for eleven reason codes.

Corrected: **C1 → 3.5h** (after the signing slice moves out, below), **C3 → 4.5h**.

### Change 1 — the agent signing kit moves to Track P as new phase A4c (1.5h)

The chat *signs* RFC 9421 requests; the sidecar *verifies* them. Same specification from two ends, and A4 is already writing the verifier. Split across two builders in two languages, the signature base gets debugged twice by two people who each assume the other side is wrong.

A4c gives both to one person in one sitting, against one shared fixture set asserted in **both** test suites. The files live in `demo/buyer-chat/src/lib/identity/` and are scheduled **first on day 2**, because C1 consumes them and because A4's verifier is still fresh from the previous evening.

**This does not touch the import firewall.** The firewall forbids imports across roots, not authorship — a person is not a root. The files import nothing from the sidecar and reach it only over HTTP.

### Change 2 — reuse two libraries instead of hand-rolling

- `http-message-signatures` (npm) for the RFC 9421 signature base, rather than writing it. It is the fiddly part and it is someone else's solved problem. ES256 itself needs no dependency — Node's built-in WebCrypto covers P-256/SHA-256.
- A headless table/form primitive for B3's variant matrix; the design system supplies the styling either way. **B3 3.5h → 3.0h.**

### Change 3 — the configuration is now decided at hour 0, not discovered at hour 22

New block in §12 with two options, one of which must be chosen and written here before any code:

- **Option A — a third builder takes Surface C.** B and C share no state, no code and no database. Surface C is 11.5h and lifts out cleanly; Track S then runs Surface B alone at 16.5h against 26.0, and **cuts 4a and 5 are not needed**. Costs the demo nothing. Take this if any third person exists.
- **Option B — two builders, cuts 5 and 4a taken up front.** Cut 5 drops the direct storefront checkout (−1.5h, cheapest cut in the plan — that path takes no money anyway). Cut 4a **trims** the admin rather than gutting it (−1.5h): keep create/edit, variant matrix, stock, price, dispatch, refund; drop CSV, bulk archive and pricing-setup CRUD. Explicitly **not** the older blunt "read-only admin" cut, which trades away Surface 2's entire reason to exist for the same ninety minutes.

### A second arithmetic error, found while applying the above

Audit #1's table omitted the three integration windows — 1.5h where neither track is building. Adding them: **both tracks now total 26.5h against 26.0 available, −0.5h each.** The earlier "+1.0h margin" on Track P was wrong.

Without cuts 4a and 5, Track S is **29.5h against 26.0 — three and a half hours over**, which is the number that makes the hour-0 decision non-optional.

The −0.5h is absorbed by the polish pass, which is budgeted 1.5h and scheduled 0.5h. Recorded in the plan as a deliberate choice: **polish is the buffer; the rehearsal is not.** An unrehearsed demo wastes the whole plan; plain empty states go unnoticed.

### Also changed

- Day 1 and Day 2 tables rebuilt against the corrected budgets, with B2 explicitly finishing by hour 10 because Integration 1 depends on it, and C2 by 17.5 because Integration 2 does.
- Cut list renumbered: 4a and 5 are spent under Option B and no longer available. The remaining list now notes that **cuts 1–3 are all Track P, the track with margin** — so their real value is freeing a Track P builder to move onto C3, not the hours themselves.
- §5 layout shows `lib/identity/` with a note on who authors it.

---

## 2026-09-20 · Audit #1 of SPECS/PLAN.md

Full read-through of the plan against `SPEC.md`, `CONTEXT.md`, `docs/adr/0017`, `0018`, and the actual code on the `main` branch. Every claim about `main` was checked by reading the file, not by memory.

**Verdict: the plan was directionally right and had 8 things in it that would have cost real hours or broken the demo.** All 26 findings below are fixed in the same commit. Severity is about consequence, not about how wrong the sentence was.

### Blockers — would have broken the build or the demo

| # | Finding | Fix |
|---|---|---|
| B-1 | **`.localhost` does not resolve inside containers.** The chat container fetching `spoiledduckie.localhost` hits its own loopback, not the proxy. Worse, the SSRF rule is "HTTPS only" and the compose fetch is HTTP — the dev exception covered hostnames but not the scheme, so the demo's own self-registration would have been refused by its own hardening. | New §10.1 "Two addresses for one shop": browser origin vs container origin, named explicitly. Dev exception now covers scheme **and** host, both fenced. |
| B-2 | **Receipt viewer was at `/agentic/receipt/<id>`** — inside the session-authenticated Merchant console. The spec requires a receipt to open by unguessable ID with **no login**. As written, no Consumer could ever open their own receipt. | Moved to public `/receipt/<id>`, added to the mount table, and the auth boundary is now stated per route. |
| B-3 | **Door 3 `reserve` took `cart_id` but keyed idempotency on `order_id:attempt`.** Contradiction: `reserve` runs after `orders.create`, so it has an `order_id`. | Door 3 input corrected to `order_id`. A call-sequence diagram added so the ordering cannot be misread. |
| B-4 | **`order_salt` custody was undefined.** The sidecar must have it to compute `destination_hash` / `contact_hash`, but the spec says it "lives only in the Merchant order row." Who generates it, how it reaches the sidecar, and why the sidecar does not persist it were all unwritten — and getting this wrong silently breaks ADR-0011's erasure guarantee. | New §6.6 "Where `order_salt` lives": Merchant generates at `orders.create`, returns it in that response only, sidecar holds it in request memory and never writes it. A test asserts it appears in no sidecar table and no log. |
| B-5 | **COD receipt sealing time was undefined.** Money moves days after `confirmed`, so "the bundle is sealed at payment" has no meaning on the cash path. | Stated: sealed at `confirmed` with an empty `moved`, and collection appends a v2 `moved` entry through the same versioning path refunds already use. No new machinery. |
| B-6 | **Schedule arithmetic was wrong and Track S was over-subscribed.** C3 (3h) + C4 (2h) were slotted into one 3h block; B1 was labelled 4h but occupied 6h of slots; Track S totalled ~25h against ~23h of slots. The plan also never said how many people it assumes. | §12 rebuilt: every phase hour re-summed, C5 moved to Track P, staffing stated in the first line (**two builders, 13h days**), and per-track totals printed against available hours so the margin is visible instead of implied. |
| B-7 | **`main`'s `create_refund_entry` keys idempotency on `checkout_id` alone.** A second partial refund returns the first entry and **writes nothing, silently**. The plan lists partial refunds as in-scope and lists this file as near-verbatim reuse — copying it would have shipped that bug. | Flagged in the reuse map as a named trap with the required fix (key on `refund_id`), and an explicit test added to A3's DONE WHEN. |
| B-8 | **`chat.localhost` had no Caddy site block.** Nothing served it. | Added to the compose/proxy section. |

### Logic errors — wrong, but recoverable

| # | Finding | Fix |
|---|---|---|
| L-1 | §6.5 check 1 named only `passkey` and `upi-pin`; a reader of the numbered list alone would mishandle `confirmed-intent`. | Check 1 now points at the Binding table as normative. |
| L-2 | `quote-fresh` is check 11, described as "immediately before `RESERVE`", while `method-enabled` is check 12 — implying a check runs after the reserve. The actual call sequence was never written down. | Explicit call sequence added to §6.5. SPEC's frozen check order is unchanged; what is now stated is that **all twelve checks are inside `decide()`, and `RESERVE` follows `decide()` returning**. |
| L-3 | Transcript check results were implicitly pass/fail, but `upi-pin` defers check 1 — so the Transcript would record a **pass for an authority that has not arrived**, which is a false statement in signed evidence. | Third result state `deferred` added to the closed set, with `settle()` required to resolve every deferred check before capture. |
| L-4 | `settle()` on COD was described as running at order time *and* capturing days later — two moments in one function. | Third entry point `collect()` named explicitly, with what it may and may not do. |
| L-5 | `consumer_id = HMAC(key, payer_handle)` — but a COD order authorized by the `passkey` mechanism has **no payer handle at all**. | Fallback stated: the passkey credential ID is the handle source on that path, recorded in the Transcript so the derivation is never ambiguous. |
| L-6 | `design/` firewall permitted only `.css/.woff2/.svg/LICENSE-*`, and §4 then put a `README.md` in it. The firewall test would have failed on the design system's own documentation. | `.md` added to the permitted list. |
| L-7 | B1 said `expires_at` carries "two deadlines"; A4 said three. | Reconciled to three. |
| L-8 | A5 said the verifier "prints both" after listing three things. | Corrected. |
| L-9 | Seed claimed 12 groups → "~20" Catalogue Items; the enumeration yields 15. | Exact counts written out per group so the seeder is a checklist, not an estimate. |
| L-10 | The seeded COD order was merchant-side only, so it could not have a sidecar Ledger or appear in `/agentic` — "an empty Ledger" was vacuously true for the wrong reason. | Seeding rule added: any order that must be visible on both sides is created by **driving the real flow** in a script, never by inserting rows. |
| L-11 | The storefront's 30-minute direct-cart stock hold had no table and no sweeper. | `site_carts` table and its sweeper added to B1. |
| L-12 | HTTP status per refusal was ambiguous — `variant-required` could reasonably be 400 or 409, and two implementers would have chosen differently. | Status is now derived from the reason code by **one table in `core/codes.py`**, never chosen at the call site. |
| L-13 | §5 layout omitted `confirmed_intent.py`. | Added. |
| L-14 | A3 and B2 both claimed the 50-concurrent-on-5-units test without distinguishing the fake from the real store. | Disambiguated: A3 runs it against the conformance fake, B2 against Postgres. Both are required; they test different things. |
| L-15 | **Ledger shape was unstated.** `main` is double-entry with named accounts (`merchant_revenue`, `customer`); the plan's invariant formulas (`sum(REFUND) ≤ captured`) assume one row per money event. An implementer copying `main` would have produced a ledger whose arithmetic does not match the plan's. | Stated explicitly: **single entry per money event**, `main`'s account legs are deliberately not carried over, and the reason is written down. |

### Unverified claims — softened rather than asserted

| # | Finding | Fix |
|---|---|---|
| U-1 | "Open Sauce Sans is OFL" was asserted from memory. The fonts folder ships `LICENSE-EBGaramond.txt` and `LICENSE-Iosevka.txt` and **no licence for Open Sauce Sans or PP Kyoto**. | Downgraded to "believed OFL, licence file absent — verify before anything public", alongside the existing PP Kyoto flag. |
| U-2 | "`localhost` is a secure context, so WebAuthn works" — true in Chrome and Firefox for `*.localhost`; **Safari does not resolve `*.localhost` at all**. | Browser support stated, with the `/etc/hosts` fallback written out. |
| U-3 | The Quote example carried `"amount_minor": -0`, which is not a meaningful JSON literal and could be copied as one. | Replaced with a real negative value, and the whole block labelled shape-not-literal. |

### Verified as correct (checked, not assumed)

- Line counts in the reuse map: `compiler.py` 322, `ledger.py` 374, `poai.py` 518, `webauthn_rp.py` 621, `oauth.py` 501, `idempotency.py` 156, `verify/checks.py` 660, `verify/cli.py` 181, `mcp_server.py` 1859, `wellknown.py` 296, `webhooks.py` 286, `inventory.py` 792. `virtual_authenticator.py` is **204** (plan said ~180 — corrected) and `core/health.py` is **227**.
- `core/health.py`, `core/database.py`, `core/audit.py`, `config.py` all exist on `main` as cited.
- The `rebuild` branch genuinely has no `src/` — this is a greenfield build on top of `main`, as the plan says.
- ADR-0017's Binding table, ADR-0018's COD ledger rules, and the `confirmed-intent` two-mechanism closed set are quoted accurately.
- The portfolio design system is at `~/projects/portfolío/frontend` (accented `í`), SvelteKit 2 / Svelte 5 / Tailwind v4, with 9 woff2 files and 2 licences — counted, not estimated.

### Added for execution safety

The plan was written to be *read*. It is now written to be *executed*, including by someone or something that will not push back when a step is ambiguous:

- **§0 Execution protocol** — the order phases run in, the commit discipline, and three standing rules: never invent an identifier, never widen scope to make a test pass, stop and ask rather than guess.
- **Per-phase file checklists** — each phase now names the files it creates, so "done" is countable.
- **Integration points as runnable commands** — the three ★ gates in §12 are now commands with expected output, not descriptions.

---

## 2026-09-20 · SPECS/PLAN.md — D1 and D4 settled

- **D1 (merchant site stack)** resolved to **SvelteKit**, on the operator's "whichever is easiest." It is the easier path: the portfolio design system is already SvelteKit 2 / Svelte 5 / Tailwind v4, so it becomes a dependency rather than something to re-implement in Next.js. Costs a one-line amendment to `SPEC.md §1` and `PLAN-merchant-site.md`, which lands in the hour-0 contracts commit.
- **D4 (COD)** flipped from **cut** to **in**, on the operator's "let COD in, it's easiest." Verified against ADR-0018 and ADR-0017 before flipping: COD is the cheapest money path in the product — no provider call, no payment link, no webhook, no link-expiry sweep, no reconcile. Cost is four specific additions (`confirmed-intent` Authority, `CAPTURE` with no preceding `RESERVE`, a third `expires_at` deadline, RTO as `cancelled`+`rto`), budgeted at 2h and ranked **cut #6 — last**, because by the time it is built the Ledger work it demonstrates is already done.
- Propagated through 14 sections: closed sets, the Binding table, Ledger rules, order lifecycle, receipt honesty, schema, admin actions, chat copy, four new test gates, the schedule, and a 7th demo beat.

## 2026-09-20 · SPECS/PLAN.md created

Comprehensive 48-hour MVP build plan covering all three surfaces, written against `SPEC.md` with departures named in §2. Grounded in a survey of the `main` branch (~7,000 reusable lines identified, ~7,000 deliberately left behind) and the portfolio design system.
