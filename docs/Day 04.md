# Day 4 — Checkout, OTP, Mandate, Razorpay, Webhooks

**Date: Saturday, August 29, 2026.**

**This is the highest-risk day in the entire project.** Protect it: block out the full day, minimize interruptions, and don't let yourself get pulled into polishing yesterday's work instead of starting this. Everything that makes OpenStore's thesis true — the signed mandate, the spend cap, the idempotent retry — gets built today. If something has to slip, it should be tomorrow's buyer-agent polish, not this.

## What you'll have by tonight

A complete, curl-driven, end-to-end money flow: a cart becomes a checkout, an OTP gets DM'd to you by a second Discord bot, you approve it, a cryptographically signed mandate gets issued, confirming it creates a real Razorpay test-mode payment link, paying that link triggers a webhook, and the webhook flips your order to PAID — with zero polling anywhere in that chain.

---

## Concepts

### 1. The checkout state machine, and why it's drawn as a diagram at all

```
PENDING ──initiate──> AWAITING_APPROVAL ──otp ok──> MANDATE_ISSUED ──confirm──> ORDER_CREATED
   │                        │                            │                          │
   └── expired/over-cap ────┴── otp fail x3 ─────────> REJECTED              webhook ▼
                                                                          PAID / FAILED
```

A **state machine** is a system that's always in exactly one of a fixed set of named states, and can only move between states along explicitly defined transitions. The value of drawing this _before_ writing code is that it makes illegal transitions visible as things that are simply absent from the diagram — there is no arrow from `PENDING` directly to `ORDER_CREATED`, which means your code should have no path that skips straight there either. Every one of today's functions checks "is the checkout currently in the state I expect before I do anything" before proceeding, and refuses (with a trace) if not. This is what makes replay attacks, double-submission, and out-of-order calls fail safely instead of silently corrupting state.

### 2. What an OTP actually is, and why it's hashed at rest

**OTP** (One-Time Password) here is a 6-digit code, generated server-side, delivered to the human out-of-band (via Discord DM, in our case), that the human types back in to prove "yes, a person who received this DM is confirming this specific checkout." Two properties matter for how you implement it:

- **Single-use and short-TTL**: once verified (or after 3 failed attempts, or after its TTL expires), it's dead — reusing a spent or expired OTP must fail. This bounds the window an attacker has if they somehow observe the code.
- **Hashed at rest, never stored in plaintext.** If your database were ever read by an attacker (SQL injection, a backup leak, an insider), a plaintext OTP column would hand them a currently-valid code to complete a fraudulent checkout. Hashing it means even a full database read only gives an attacker a hash they can't reverse into the original 6 digits (assuming they don't also brute-force it within the TTL, which is why _short_ TTL and _few_ attempts matter as much as hashing).

You'll use **`secrets`** (the same module from Day 2's PKCE work) to generate the 6-digit code, and **Argon2** to hash it. Argon2 is a modern password-hashing algorithm (winner of the 2015 Password Hashing Competition) purpose-built to be slow and memory-hard — unlike a general-purpose hash like SHA-256 (fast, meant for integrity checks, not secrecy), Argon2 is deliberately expensive to compute, which makes brute-forcing a stolen hash far more costly per guess. For a 6-digit numeric OTP (only 1,000,000 possible values) this matters more than it might for a long random secret — the attempt-limit and TTL are still your primary defenses, but hashing with a slow algorithm is the correct baseline practice regardless.

```bash
uv add argon2-cffi
```

```python
from argon2 import PasswordHasher
ph = PasswordHasher()

otp = "".join(secrets.choice("0123456789") for _ in range(6))
otp_hash = ph.hash(otp)
# later, to verify:
try:
    ph.verify(otp_hash, submitted_otp)
    valid = True
except Exception:
    valid = False
```

### 3. Canonical JSON — why "sorted keys, tight separators, no floats" is load-bearing

You're about to cryptographically sign a JSON payload (the mandate). A signature is computed over the _exact bytes_ you feed the signing function — if the verifier reconstructs even slightly different bytes (different key order, different whitespace, a trailing decimal `.0` where the signer had a bare integer), the signature check fails, because it's a different byte string even though it represents "the same" logical data.

**Canonical JSON** is a specific, deterministic way of serializing a JSON-representable value to bytes, such that anyone following the same rules always produces byte-for-byte identical output for the same logical data. The plan specifies "RFC-8785-style" — [RFC 8785 (JCS, JSON Canonicalization Scheme)](https://www.rfc-editor.org/rfc/rfc8785) — whose core rules are: object keys sorted lexicographically, no insignificant whitespace, UTF-8 encoding, and strict numeric formatting rules that specifically make floating-point representation inconsistent across languages/platforms (part of why minor-unit integers, never floats, matter doubly here — Day 1's rule wasn't just about avoiding rounding error, it's also what makes canonical serialization unambiguous).

In Python, you get most of the way there with `json.dumps`'s own options:

```python
import json

def canonical_json_bytes(payload: dict) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),  # no spaces after , or :
        ensure_ascii=False,
    ).encode("utf-8")
```

`sort_keys=True` handles key ordering. `separators=(",", ":")` removes the default `", "` and `": "` spacing Python's `json.dumps` normally inserts — "tight separators" from the plan. This is sufficient for our purposes as long as every value in the payload is genuinely an `int` (never a `float`) and every string field is already normalized — which is exactly why the mandate payload's `amt` field is `int` (minor units) and the `cart.items[].unit_minor` fields are `int`, never `float`, matching Day 1's rule.

### 4. Ed25519 and JWS — asymmetric signing, and why it's asymmetric here specifically

Day 2's OAuth access tokens used **symmetric** signing (`HS256`): the same secret both signs and verifies, which is fine because only your own merchant server ever needs to verify its own tokens. The mandate is different: recall the architecture boundary from the top-level thesis — the **merchant reasoning agent** (a separate process, with a read-only DB session, explicitly holding **no signing key**) must never be able to forge a mandate, even though it's part of the same overall system and might get compromised or manipulated.

**Asymmetric signing** uses a **key pair**: a private key that only the merchant _execution_ server holds, and a public key anyone (the reasoning agent, the buyer agent, an auditor) can have, to _verify_ a signature without ever being able to produce one. **Ed25519** is a specific, modern, fast, safe-by-default elliptic-curve signature algorithm (part of the EdDSA family) — chosen here over the older RSA for smaller keys/signatures and fewer footguns (no padding-scheme choices to get wrong, unlike RSA's PKCS#1 v1.5 vs PSS distinction).

```python
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

private_key = Ed25519PrivateKey.generate()
public_key = private_key.public_key()

signature = private_key.sign(canonical_json_bytes(payload))
# anyone with public_key can verify:
public_key.verify(signature, canonical_json_bytes(payload))  # raises if invalid
```

**JWS** (JSON Web Signature) is the standard _envelope format_ for a signed payload — it's what turns "a payload and a raw signature" into a single, standard, transportable string: `base64url(header).base64url(payload).base64url(signature)`, the same three-segment shape as a JWT (a JWT is, in fact, one specific application of JWS, where the payload happens to be a set of claims). You'll produce this using `pyjwt`, which supports Ed25519 (via `alg="EdDSA"`) even though the library's name suggests only "JWT":

```python
import jwt as pyjwt

jws_compact = pyjwt.encode(
    payload,
    private_key,          # pyjwt accepts a cryptography key object directly for EdDSA
    algorithm="EdDSA",
    headers={"kid": "merchant-key-1"},
)
```

The **`kid`** (key ID) header lets a verifier with multiple keys on file (e.g., after a key rotation) know which public key to check the signature against, without guessing. **Pin `alg=EdDSA` and reject `alg=none`** on verification, explicitly, the same discipline as Day 2's `algorithms=["HS256"]` allowlist — this is the exact same "don't let the token dictate its own verification method" principle, applied to a higher-stakes artifact.

### 5. The fingerprint — showing a mandate without exposing it

The plan is explicit: _"the full JWS never appears in a chat channel."_ The reason is that the full JWS **contains the entire mandate payload in plaintext** (recall from Day 2: JWTs/JWS are only base64-encoded, not encrypted) — pasting it into a Discord channel would broadcast the cart contents, delivery address hash, and amount to anyone who can read that channel, and more importantly would give anyone who copies that string a token that, while it can't be _forged_, can potentially be _replayed_ if your `checkout_confirm` logic doesn't correctly burn the `jti` (single-use marker) after use.

Instead, you compute and display only a **fingerprint**: `sha256(jws_compact)[:8]`, a short hex string that's effectively a nickname for "this exact signed mandate" — enough for a human to visually correlate "the fingerprint in my DM matches the fingerprint the bot echoed back after I approved, matches the fingerprint in the audit trail," without ever exposing the signable payload itself in chat. This becomes one of the three "moments that win the rubric" from the plan's Day 9 demo script — the fingerprint appearing identically in three places (DM, bot reply, audit trail) is a visceral, checkable proof that the approval a human gave really is the same object the server later confirmed.

### 6. Idempotency — making retries safe

An **idempotency key** is a client-supplied identifier for "this logical operation," such that if the same key is submitted twice (e.g., because a network timeout made the client think the first attempt failed and it retried), the server recognizes the repeat and returns the _original_ result instead of executing the operation a second time.

```python
class IdempotencyRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: str = Field(index=True)
    idempotency_key: str = Field(index=True)
    response_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

(Add this table to `merchant/models.py` now — it's new today, unlike most of Day 1's schema which just sat unused until now.)

The pattern: before doing the actual work, check if `(client_id, idempotency_key)` already has a stored response — if so, **return that stored response immediately, without re-executing anything**. If not, do the work, and — this ordering matters — **persist the result _before_ returning it to the caller**, so that even if the response never makes it back to the client (the exact "timeout after success" scenario Day 7 deliberately simulates), the _next_ retry with the same key finds the already-completed result rather than re-running a real Razorpay charge.

This is precisely Day 7's strongest demo beat: `/demo/inject-timeout` will deliberately drop the response _after_ the Razorpay order is created, so you can show the agent retrying with the same idempotency key and getting the original result back — zero duplicate charges, zero duplicate orders — purely because this pattern is correct.

### 7. Webhooks: why the server is the source of truth, never polling

You could, in principle, have your server repeatedly ask Razorpay "has this payment gone through yet?" (**polling**) — but this wastes requests, adds latency (you only find out on your next poll interval), and doesn't scale. A **webhook** flips the direction: Razorpay itself makes an HTTP POST to _your_ server the moment a payment event happens, carrying the event data. Your server just needs a public URL to receive it.

Since your development machine likely isn't reachable from the public internet, you need a **tunnel** — **`cloudflared`**'s "quick tunnel" mode spins up a temporary public HTTPS URL that forwards to a port on your local machine, no account or DNS setup required:

```bash
cloudflared tunnel --url http://localhost:8000
```

This prints a random `https://<something>.trycloudflare.com` URL. You register _that_ URL (plus your webhook path, e.g. `/webhooks/razorpay`) with Razorpay's dashboard as your webhook endpoint. Every time you restart the tunnel, the URL changes (this is the plan's Risk R4 — "cloudflared tunnel URL churn" — you'll handle re-registering it in your startup script on Day 8, but be aware of it now).

**Webhook signature verification** matters because your webhook endpoint is, necessarily, a public URL — anyone who guesses or discovers it could POST a fake "payment succeeded" event if you don't verify it really came from Razorpay. Razorpay signs each webhook payload with **HMAC-SHA256**, using a shared secret you configure in the dashboard: it computes `HMAC-SHA256(webhook_secret, raw_request_body)` and sends the result in an `X-Razorpay-Signature` header. You independently compute the same HMAC over the raw bytes you received, and compare — a mismatch means either the payload was tampered with in transit, or it didn't really come from Razorpay.

```python
import hmac
import hashlib

def verify_razorpay_signature(raw_body: bytes, signature: str, webhook_secret: str) -> bool:
    expected = hmac.new(webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
```

`hmac.compare_digest` (rather than `expected == signature`) matters: a naive `==` string comparison on most implementations short-circuits at the first differing character, meaning the comparison takes _slightly_ less time the earlier the strings diverge — in principle, a sufficiently patient network attacker measuring response times could exploit that timing difference to guess the correct signature one byte at a time (a **timing attack**). `compare_digest` is specifically implemented to take the same amount of time regardless of where or whether the strings differ, closing that side channel. This is a small function but the right habit to build now, since you'll want the same discipline anywhere you compare a secret/hash to an attacker-controlled input.

**Deduplication on event id**: Razorpay (like most webhook providers) does not guarantee exactly-once delivery — it may retry a webhook it thinks failed to deliver, even if your server actually processed it successfully. Every event has a unique ID; store it (the `WebhookEvent.razorpay_event_id` column, already in your Day 1 schema, with a `unique=True` constraint) and check for it before processing — if you've seen this event ID before, acknowledge and do nothing further.

---

## Build

### Step 1 — Generate and store the Ed25519 keypair

**What**: generate the merchant's signing keypair once, and persist the private key somewhere your process can load it from on startup. **Tool**: `cryptography`'s `Ed25519PrivateKey`. **Why**: you need this key to exist and be stable across restarts — regenerating it every process start would invalidate every previously issued mandate's verifiability.

```python
# scripts/generate_mandate_key.py — run this ONCE, not on every app startup
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

private_key = Ed25519PrivateKey.generate()
pem = private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)
with open("merchant_signing_key.pem", "wb") as f:
    f.write(pem)
print("Key written to merchant_signing_key.pem — add this filename to .gitignore NOW")
```

```bash
uv run python scripts/generate_mandate_key.py
echo "merchant_signing_key.pem" >> .gitignore
```

`merchant/mandate.py`:

```python
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

with open("merchant_signing_key.pem", "rb") as f:
    _PRIVATE_KEY: Ed25519PrivateKey = serialization.load_pem_private_key(f.read(), password=None)
_PUBLIC_KEY = _PRIVATE_KEY.public_key()
KID = "merchant-key-1"
```

### Step 2 — Canonical JSON, sign, and verify

```python
import json
import time
import uuid
import hashlib
import jwt as pyjwt
from merchant.models import MandatePayload

ALLOWED_ALGS = ["EdDSA"]
MANDATE_TTL_SECONDS = 120

def canonical_json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

def issue_mandate(merchant_id: str, checkout_id: str, client_id: str, cart_hash: str,
                   cart_version: int, items: list[dict], total_minor: int,
                   delivery_address_hash: str) -> tuple[str, str]:
    now = int(time.time())
    payload = MandatePayload(
        iss=merchant_id,
        jti=str(uuid.uuid4()),
        chk=checkout_id,
        sub=client_id,
        cart={"hash": cart_hash, "version": cart_version, "items": items},
        iat=now,
        exp=now + MANDATE_TTL_SECONDS,
        amt=total_minor,
        nonce=uuid.uuid4().hex,
        dlv=delivery_address_hash,
    ).model_dump()

    jws_compact = pyjwt.encode(payload, _PRIVATE_KEY, algorithm="EdDSA", headers={"kid": KID})
    fingerprint = hashlib.sha256(jws_compact.encode()).hexdigest()[:8]
    return jws_compact, fingerprint

def verify_mandate(jws_compact: str) -> dict:
    """Raises jwt exceptions on any failure. Caller is responsible for the
    checkout-state / cart-hash / jti-unburned / spend-cap checks that follow —
    this function ONLY proves the signature and basic claims are valid."""
    header = pyjwt.get_unverified_header(jws_compact)
    if header.get("alg") not in ALLOWED_ALGS:
        raise ValueError(f"Rejected alg: {header.get('alg')}")
    return pyjwt.decode(jws_compact, _PUBLIC_KEY, algorithms=ALLOWED_ALGS)
```

Note `issue_mandate` builds the payload through the `MandatePayload` Pydantic model you froze on Day 1 — this is the payoff of freezing contracts early: today's mandate-issuing code and Day 5/6's mandate-_reading_ code (in a different process entirely) both import the same shape and can't silently drift apart.

### Step 3 — `checkout_initiate`: freeze the snapshot, check the cap, issue the OTP

**What**: the tool that takes a `cart_id`, recomputes everything server-side, checks spend caps, freezes an immutable snapshot as a `Checkout` row, and triggers OTP delivery. **Tool**: everything from Steps 1–2 of Day 3 (rate limits, scopes) plus the spend-cap logic from Day 3's Concepts, plus today's OTP hashing.

```python
import hashlib
import secrets
from datetime import datetime, timedelta
from argon2 import PasswordHasher
from merchant.models import Cart, Checkout, OTPChallenge
from merchant.policy import rolling_spend_minor
from merchant.notifier import send_approval_dm  # built in Step 4

ph = PasswordHasher()
PER_TX_CAP_MINOR = 50000       # from agent-commerce.json policy.default_per_tx_cap_minor
ROLLING_CAP_MINOR = 200000     # example 24h rolling cap
OTP_TTL_MINUTES = 5
OTP_MAX_ATTEMPTS = 3

def compute_cart_hash(items: list[dict]) -> str:
    return hashlib.sha256(canonical_json_bytes({"items": items})).hexdigest()

@mcp.tool()
@audited_tool("checkout_initiate")
def checkout_initiate(cart_id: int, delivery_address: str, ctx: Context, trace_id: str = None) -> dict:
    """Initiates checkout for a cart. Recomputes the total from the DB, checks
    spend caps, freezes an immutable snapshot, and sends an OTP to the human
    approver via DM. Never touches Razorpay."""
    claims = get_claims_from_context(ctx, "checkout:initiate")
    if not check_rate_limit(claims["sub"], "checkout_initiate"):
        raise HTTPException(429, "Rate limit exceeded")

    with Session(engine) as session:
        cart = session.get(Cart, cart_id)
        if cart is None or cart.client_id != claims["sub"]:
            raise HTTPException(404, "Cart not found")

        total_minor = sum(item["unit_minor"] * item["qty"] for item in cart.items_json)

        if total_minor > PER_TX_CAP_MINOR:
            emit("merchant-server", "Checkout rejected: over per-tx cap",
                 {"total": total_minor, "cap": PER_TX_CAP_MINOR}, trace_id, "blocked")
            raise HTTPException(400, f"Amount {total_minor} exceeds per-transaction cap {PER_TX_CAP_MINOR}")

        already_spent = rolling_spend_minor(session, claims["sub"])
        if already_spent + total_minor > ROLLING_CAP_MINOR:
            emit("merchant-server", "Checkout rejected: over rolling cap",
                 {"already_spent": already_spent, "attempted": total_minor}, trace_id, "blocked")
            raise HTTPException(400, "Rolling 24h spend cap exceeded")

        checkout_id = str(uuid.uuid4())
        cart_hash = compute_cart_hash(cart.items_json)
        dlv_hash = hashlib.sha256(delivery_address.encode()).hexdigest()

        checkout = Checkout(
            checkout_id=checkout_id,
            cart_id=cart.id,
            client_id=claims["sub"],
            status="AWAITING_APPROVAL",
            cart_hash=cart_hash,
            total_minor=total_minor,
            delivery_address=delivery_address,
            expires_at=datetime.utcnow() + timedelta(minutes=OTP_TTL_MINUTES),
        )
        session.add(checkout)

        otp = "".join(secrets.choice("0123456789") for _ in range(6))
        otp_hash = ph.hash(otp)
        session.add(OTPChallenge(checkout_id=checkout_id, otp_hash=otp_hash))
        session.commit()

        send_approval_dm(
            checkout_id=checkout_id,
            items=cart.items_json,
            total_minor=total_minor,
            delivery_address=delivery_address,
            otp=otp,
            expires_at=checkout.expires_at,
        )

        emit("merchant-server", "Checkout initiated", {"checkout_id": checkout_id, "total": total_minor}, trace_id, "gate")
        return {"checkout_id": checkout_id, "status": "AWAITING_APPROVAL", "expires_in_seconds": OTP_TTL_MINUTES * 60}
```

Read `total_minor = sum(item["unit_minor"] * item["qty"] for item in cart.items_json)` carefully: this reads `unit_minor` from `cart.items_json`, which is the **server's own stored cart** (written by yesterday's `_validate_and_price`, which itself only ever wrote catalog-derived prices) — not anything freshly supplied by this call's arguments. The only caller-supplied inputs to this whole function are `cart_id` (a reference, not a value) and `delivery_address`. This is "never trust an agent-supplied total" made concrete a second time, one layer up the stack from Day 3.

### Step 4 — `notifier.py`: the OpenStore Merchant bot, DM + button + modal

**What**: a second, separate Discord bot application whose only job is DMing the human approver a rich embed, with an Approve button that opens a modal for OTP entry, and POSTing the submitted OTP back to your merchant server. **Tool**: `discord.py`, specifically its `Button`, `View`, and `Modal` UI components. **Why**: the plan is explicit this must be a _separate bot application_ from the buyer-facing one — the human approval surface must be something the buyer agent cannot render or influence, since the entire "architectural, not instructional" security claim rests on the approval surface being outside the reasoning agent's control.

This needs its own small always-running process, distinct from your FastAPI app. `merchant/notifier.py`:

```python
import discord
from discord import app_commands
import httpx
import os

MERCHANT_SERVER_URL = "http://localhost:8000"
APPROVER_USER_ID = int(os.environ["APPROVER_DISCORD_USER_ID"])  # you, for the demo

intents = discord.Intents.default()
bot = discord.Client(intents=intents)

class OTPModal(discord.ui.Modal, title="Enter OTP to approve"):
    otp_input = discord.ui.TextInput(label="6-digit code", min_length=6, max_length=6)

    def __init__(self, checkout_id: str):
        super().__init__()
        self.checkout_id = checkout_id

    async def on_submit(self, interaction: discord.Interaction):
        # Defer immediately — Discord requires an ack within 3 seconds (plan's Risk R2).
        await interaction.response.defer(ephemeral=True)
        resp = httpx.post(
            f"{MERCHANT_SERVER_URL}/internal/otp-verify",
            json={"checkout_id": self.checkout_id, "otp": self.otp_input.value},
            timeout=10.0,
        )
        if resp.status_code == 200:
            fingerprint = resp.json()["fingerprint"]
            await interaction.followup.send(f"✅ Approved. Mandate fingerprint: `{fingerprint}`", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ Rejected: {resp.json().get('detail')}", ephemeral=True)

class ApprovalView(discord.ui.View):
    def __init__(self, checkout_id: str):
        super().__init__(timeout=300)
        self.checkout_id = checkout_id

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(OTPModal(self.checkout_id))

def send_approval_dm(checkout_id: str, items: list[dict], total_minor: int,
                      delivery_address: str, otp: str, expires_at):
    embed = discord.Embed(title="OpenStore — Checkout Approval Needed", color=0xF1C40F)
    for item in items:
        embed.add_field(name=item["sku"], value=f"qty {item['qty']} @ ₹{item['unit_minor']/100:.2f}", inline=False)
    embed.add_field(name="Total", value=f"₹{total_minor/100:.2f}", inline=True)
    embed.add_field(name="Delivery address", value=delivery_address, inline=False)
    embed.add_field(name="Expires", value=expires_at.isoformat(), inline=True)
    embed.set_footer(text=f"checkout_id={checkout_id}")

    async def _send():
        user = await bot.fetch_user(APPROVER_USER_ID)
        await user.send(embed=embed, view=ApprovalView(checkout_id))

    bot.loop.create_task(_send())
```

**The OTP itself is deliberately not shown in this embed** — it's delivered separately (in practice: the same DM could include it in a second, plainer message, or — a cleaner design — the human is simply expected to already know it's a 6-digit code and enters what they received; adjust based on exactly how you want the demo to read, but do not put the OTP in the same rich embed as the cart details, since that undermines the "OTP proves you specifically saw and typed something" property). A clean approach: send the OTP as a separate plain DM message immediately after the embed, so the embed (reusable for e-log/screenshot purposes) never contains the secret itself.

Notice `await interaction.response.defer(ephemeral=True)` as the _very first_ line of `on_submit` — this is Risk R2 from the plan, called out explicitly: Discord requires you to acknowledge any interaction (button click, modal submit) within 3 seconds, or Discord shows the user an "interaction failed" error, even if your code is still working correctly in the background. `defer()` immediately satisfies that deadline, buying you time to do the actual HTTP call to your merchant server before using `followup.send(...)` (rather than the original `interaction.response`) to deliver the real result once it's ready. This is exactly why the plan says to prototype the modal on Day 4, not Day 8 — this specific timing constraint is easy to get wrong the first time you meet it, and you don't want to be debugging it for the first time during freeze week.

Add `merchant/internal_routes.py` for the OTP verification endpoint the bot calls:

```python
from merchant.mandate import issue_mandate, compute_cart_hash
from merchant.models import Checkout, OTPChallenge

@app.post("/internal/otp-verify")
def otp_verify(checkout_id: str = Body(...), otp: str = Body(...), session: Session = Depends(get_session)):
    checkout = session.exec(select(Checkout).where(Checkout.checkout_id == checkout_id)).first()
    if checkout is None or checkout.status != "AWAITING_APPROVAL":
        raise HTTPException(400, "No pending checkout in this state")
    if checkout.expires_at < datetime.utcnow():
        checkout.status = "REJECTED"
        session.add(checkout)
        session.commit()
        raise HTTPException(400, "Checkout expired")

    challenge = session.exec(
        select(OTPChallenge).where(OTPChallenge.checkout_id == checkout_id).where(OTPChallenge.used == False)
    ).first()
    if challenge is None or challenge.attempts >= OTP_MAX_ATTEMPTS:
        raise HTTPException(400, "No valid OTP challenge (expired or too many attempts)")

    try:
        ph.verify(challenge.otp_hash, otp)
    except Exception:
        challenge.attempts += 1
        session.add(challenge)
        session.commit()
        if challenge.attempts >= OTP_MAX_ATTEMPTS:
            checkout.status = "REJECTED"
            session.add(checkout)
            session.commit()
        raise HTTPException(400, "Incorrect OTP")

    challenge.used = True
    session.add(challenge)

    cart = session.get(Cart, checkout.cart_id)
    jws_compact, fingerprint = issue_mandate(
        merchant_id="gelateria-roma",
        checkout_id=checkout_id,
        client_id=checkout.client_id,
        cart_hash=checkout.cart_hash,
        cart_version=cart.version,
        items=cart.items_json,
        total_minor=checkout.total_minor,
        delivery_address_hash=hashlib.sha256(checkout.delivery_address.encode()).hexdigest(),
    )
    checkout.status = "MANDATE_ISSUED"
    session.add(checkout)
    session.add(Mandate(
        jti=pyjwt.decode(jws_compact, options={"verify_signature": False})["jti"],
        checkout_id=checkout_id, jws_compact=jws_compact, fingerprint=fingerprint,
    ))
    session.commit()

    return {"fingerprint": fingerprint, "jws": jws_compact}
```

This endpoint is intentionally **not** an MCP tool — it's called only by your own trusted `notifier.py` process, never by an agent. It's the literal implementation of "OTP verify → sign JWS" from Day 1's MCP tool table (`get_signed_mandate`'s gate), just wired through the notifier instead of exposed as an agent-callable tool. The `jws_compact` is returned here so the notifier could log it locally if needed, but note it is never sent back to Discord — only the `fingerprint` goes into the modal's response, matching the "full JWS never appears in a chat channel" rule.

### Step 5 — `razorpay_client.py`: order creation and payment links

**What**: a thin wrapper around the `razorpay-python` SDK for creating an order and a payment link, plus a demo fault-injection toggle for Day 7.

```python
import razorpay
import os

_client = razorpay.Client(auth=(os.environ["RAZORPAY_KEY_ID"], os.environ["RAZORPAY_KEY_SECRET"]))

INJECT_TIMEOUT = {"enabled": False}  # toggled by /demo/inject-timeout on Day 7

def create_order_and_payment_link(amount_minor: int, checkout_id: str) -> dict:
    order = _client.order.create({
        "amount": amount_minor,
        "currency": "INR",
        "receipt": checkout_id,
    })
    link = _client.payment_link.create({
        "amount": amount_minor,
        "currency": "INR",
        "reference_id": checkout_id,
        "notes": {"order_id": order["id"]},
    })
    return {"razorpay_order_id": order["id"], "payment_link_id": link["id"], "payment_link_url": link["short_url"]}
```

You need a real Razorpay test-mode key ID and secret in `.env` for this to work — from the account you created on Day 1 (per the prerequisites checklist).

### Step 6 — `checkout_confirm`: the full verification ladder, then Razorpay, with idempotency

**What**: the tool that verifies the mandate, re-checks everything, and — only after every check passes — calls Razorpay, persisting the result before returning. **Tool**: everything built so far today, plus the `IdempotencyRecord` table from Concepts.

```python
from merchant.models import IdempotencyRecord, Order
from merchant.razorpay_client import create_order_and_payment_link, INJECT_TIMEOUT

@mcp.tool()
@audited_tool("checkout_confirm")
def checkout_confirm(jws: str, idempotency_key: str, ctx: Context, trace_id: str = None) -> dict:
    """Confirms a checkout given a signed mandate. Runs the full verification
    ladder — signature, claims, checkout state, cart hash, amount, jti unburned,
    spend cap re-check, idempotency — before ever calling Razorpay."""
    claims = get_claims_from_context(ctx, "checkout:confirm")
    if not check_rate_limit(claims["sub"], "checkout_confirm"):
        raise HTTPException(429, "Rate limit exceeded")

    with Session(engine) as session:
        existing = session.exec(
            select(IdempotencyRecord)
            .where(IdempotencyRecord.client_id == claims["sub"])
            .where(IdempotencyRecord.idempotency_key == idempotency_key)
        ).first()
        if existing is not None:
            emit("merchant-server", "Idempotent replay — returning cached result", {}, trace_id, "info")
            return existing.response_json

        # 1. Signature + basic claims
        try:
            mandate_claims = verify_mandate(jws)
        except Exception as e:
            emit("merchant-server", "Mandate verification failed", {"error": str(e)}, trace_id, "blocked")
            raise HTTPException(400, f"Invalid mandate: {e}")

        # 2. Expiry (belt-and-suspenders: pyjwt already checks exp, but be explicit)
        if mandate_claims["exp"] < time.time():
            emit("merchant-server", "Mandate expired", {}, trace_id, "blocked")
            raise HTTPException(400, "Mandate expired")

        checkout = session.exec(
            select(Checkout).where(Checkout.checkout_id == mandate_claims["chk"])
        ).first()

        # 3. Checkout state
        if checkout is None or checkout.status != "MANDATE_ISSUED":
            emit("merchant-server", "Checkout not in MANDATE_ISSUED state", {}, trace_id, "blocked")
            raise HTTPException(400, "Checkout not awaiting confirmation")

        # 4. Cart hash match (tampering detection)
        if mandate_claims["cart"]["hash"] != checkout.cart_hash:
            emit("merchant-server", "Cart hash mismatch — possible tampering", {}, trace_id, "blocked")
            raise HTTPException(400, "Cart hash mismatch")

        # 5. Amount match
        if mandate_claims["amt"] != checkout.total_minor:
            emit("merchant-server", "Amount mismatch", {}, trace_id, "blocked")
            raise HTTPException(400, "Amount mismatch")

        # 6. jti unburned (replay detection)
        mandate_row = session.exec(select(Mandate).where(Mandate.jti == mandate_claims["jti"])).first()
        if mandate_row is None or mandate_row.burned:
            emit("merchant-server", "Mandate replay detected", {"jti": mandate_claims["jti"]}, trace_id, "blocked")
            raise HTTPException(400, "Mandate already used or unknown")

        # 7. Spend cap re-check (state may have changed since initiate)
        already_spent = rolling_spend_minor(session, claims["sub"])
        if already_spent + checkout.total_minor > ROLLING_CAP_MINOR:
            emit("merchant-server", "Spend cap exceeded at confirm time", {}, trace_id, "blocked")
            raise HTTPException(400, "Rolling spend cap exceeded")

        # All checks passed. Burn the jti BEFORE calling Razorpay.
        mandate_row.burned = True
        session.add(mandate_row)
        session.commit()

        if INJECT_TIMEOUT["enabled"]:
            rp_result = create_order_and_payment_link(checkout.total_minor, checkout.checkout_id)
            # Persist BEFORE simulating the drop — this is the whole point.
            _persist_confirm_result(session, checkout, claims["sub"], idempotency_key, rp_result, trace_id)
            INJECT_TIMEOUT["enabled"] = False
            raise HTTPException(504, "Simulated timeout — response dropped after order creation")

        rp_result = create_order_and_payment_link(checkout.total_minor, checkout.checkout_id)
        result = _persist_confirm_result(session, checkout, claims["sub"], idempotency_key, rp_result, trace_id)
        return result


def _persist_confirm_result(session, checkout, client_id, idempotency_key, rp_result, trace_id) -> dict:
    checkout.status = "ORDER_CREATED"
    session.add(checkout)
    session.add(Order(
        checkout_id=checkout.checkout_id,
        razorpay_order_id=rp_result["razorpay_order_id"],
        razorpay_payment_link_id=rp_result["payment_link_id"],
        status="CREATED",
        total_minor=checkout.total_minor,
    ))
    session.add(SpendLedgerEntry(client_id=client_id, amount_minor=checkout.total_minor))

    result = {"checkout_id": checkout.checkout_id, "payment_link_url": rp_result["payment_link_url"], "status": "ORDER_CREATED"}
    session.add(IdempotencyRecord(client_id=client_id, idempotency_key=idempotency_key, response_json=result))
    session.commit()
    emit("merchant-server", "Order created", {"checkout_id": checkout.checkout_id}, trace_id, "executed")
    return result
```

Read the seven numbered checks in order and confirm each one maps to something in §3 of the plan's contract table — this ladder, in this exact order, _is_ the mandate's entire security value. Skipping or reordering any one of them (e.g., checking the spend cap before verifying the signature) creates a gap an attacker-controlled payload could exploit before you've even confirmed the payload is genuine.

Notice `_persist_confirm_result` is called, and its result is _committed to the database_, in both the fault-injection branch and the normal branch — the `INJECT_TIMEOUT` toggle only affects whether the HTTP response actually reaches the caller, never whether the database write happens. This is the concrete mechanism behind Day 7's idempotent-retry demo: the order genuinely gets created and stored; only the network response describing it gets dropped, simulating exactly the ambiguous "did that actually work?" situation a real client sees during a real network timeout.

### Step 7 — Webhooks: verify, dedupe, transition

**What**: the endpoint Razorpay POSTs to, which verifies the HMAC signature, checks for a duplicate event ID, and transitions the `Order` row to `PAID` or `FAILED`.

```python
from fastapi import Request
from merchant.models import WebhookEvent, Order

RAZORPAY_WEBHOOK_SECRET = os.environ["RAZORPAY_WEBHOOK_SECRET"]

@app.post("/webhooks/razorpay")
async def razorpay_webhook(request: Request, session: Session = Depends(get_session)):
    raw_body = await request.body()
    signature = request.headers.get("x-razorpay-signature", "")

    if not verify_razorpay_signature(raw_body, signature, RAZORPAY_WEBHOOK_SECRET):
        emit("merchant-server", "Webhook signature invalid", {}, str(uuid.uuid4()), "blocked")
        raise HTTPException(400, "Invalid signature")

    payload = json.loads(raw_body)
    event_id = payload["id"]

    existing = session.exec(select(WebhookEvent).where(WebhookEvent.razorpay_event_id == event_id)).first()
    if existing is not None:
        return {"status": "duplicate, ignored"}

    session.add(WebhookEvent(razorpay_event_id=event_id, event_type=payload["event"], payload_json=payload))

    if payload["event"] == "payment_link.paid":
        reference_id = payload["payload"]["payment_link"]["entity"]["reference_id"]
        order = session.exec(select(Order).where(Order.checkout_id == reference_id)).first()
        if order:
            order.status = "PAID"
            session.add(order)

    session.commit()
    emit("audit-trail", "Webhook processed", {"event": payload["event"]}, str(uuid.uuid4()), "executed")
    return {"status": "ok"}
```

`await request.body()` reads the **raw bytes** of the request body — deliberately _not_ `await request.json()` first, because HMAC verification must run over the exact bytes Razorpay signed; parsing to a Python dict and re-serializing it could produce different bytes (different key order, different whitespace) even if the logical content is unchanged, which would make a legitimate signature fail to verify. Verify first, against raw bytes; parse second, only after verification passes.

---

## Running the full end-to-end flow

Start the merchant server, the notifier bot, and a `cloudflared` tunnel (register the tunnel URL + `/webhooks/razorpay` in your Razorpay test dashboard's webhook settings, with `payment_link.paid` as the subscribed event). Then, using a valid `checkout:initiate`+`checkout:confirm`-scoped token from Day 2's flow:

```bash
# create a cart, then:
curl -X POST http://localhost:8000/agent/mcp -H "Authorization: Bearer <token>" \
  -d '{"tool": "checkout_initiate", "cart_id": 1, "delivery_address": "221B Baker Street"}'
# (exact request shape depends on your MCP client — adjust to however you're calling tools)
```

Watch for the Discord DM to arrive. Click Approve, enter the OTP in the modal, confirm you get a fingerprint back. Then call `checkout_confirm` with the returned `jws` and a fresh `idempotency_key`, get back a `payment_link_url`, open it, pay with Razorpay's test card details, and watch the webhook flip your `Order.status` to `PAID` — check this at `/admin/audit` or by querying the database directly.

## Exit check (from the plan)

> ✅ Exit: curl-driven end-to-end — cart → initiate → DM lands → OTP → mandate → confirm → real Razorpay test payment link → pay it → webhook flips the order to PAID.

Every arrow in that sentence is a thing to verify individually, not just "it worked once, moving on": cart creation returns an ID; initiate returns `AWAITING_APPROVAL`; the DM actually lands with correct amounts; OTP entry returns a fingerprint; confirm returns a real `payment_link_url` (visit it — does it actually look like a Razorpay checkout page?); paying it with test card details actually completes; and — the part people skip checking — the webhook _actually fires and your order status actually changes_, not just "the payment page said success." Check the database (or `/admin/audit`) directly for the `PAID` status; don't infer it from the payment page alone.

## What could go wrong

- **DM never arrives**: confirm the notifier bot is a _member of a server you share with the approver account_ — Discord bots can only DM users they share a server with (a very common early trip-up), and confirm `APPROVER_DISCORD_USER_ID` is your actual numeric Discord user ID (enable Developer Mode in Discord settings, right-click your own name, Copy User ID), not a username.
- **Modal submission shows "This interaction failed"**: you're not calling `interaction.response.defer(...)` fast enough, or you're calling `interaction.response.send_message(...)` _and_ `defer()` (only one initial response is allowed per interaction) — re-read Concepts section on the 3-second ack deadline.
- **Cart hash mismatch on every confirm, even with no tampering**: almost always a canonical-JSON inconsistency — confirm `compute_cart_hash` is called with _identical_ Python data structures (same key order doesn't matter since you sort, but same _value types_ do — e.g. make sure `qty` is consistently `int`, never sometimes `str`) at both initiate-time and whatever reconstructs the hash at confirm-time.
- **`pyjwt.encode` throws on Ed25519**: some `pyjwt` versions require the `cryptography` extra explicitly (`uv add "pyjwt[crypto]"`) — if signing fails with an algorithm-not-supported error, this is the first thing to check.
- **Razorpay `payment_link.create` fails with an auth error**: double-check you're using the test-mode key ID/secret (they're typically prefixed `rzp_test_`) from the correct one of your two Day 1 accounts, and that both are actually present in `.env`.
- **Webhook never fires**: verify the tunnel URL is actually registered in the Razorpay dashboard's webhook settings _for this specific test account_, that you selected the `payment_link.paid` event specifically, and that the tunnel itself is still running (quick tunnels die if the `cloudflared` process is killed — check your terminal).
- **Webhook fires but signature verification always fails**: you're likely verifying against `await request.json()`-then-reserialized bytes instead of the raw body — re-read the note at the end of Step 7. Also confirm `RAZORPAY_WEBHOOK_SECRET` matches exactly what's configured in the dashboard for that specific webhook (not your API key secret — webhooks have their own separate secret).

---

Tomorrow you finally build something that talks back: the buyer agent, in LangGraph, driven entirely from Discord chat.