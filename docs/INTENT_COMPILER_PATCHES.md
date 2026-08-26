# Change Log and Patch Document — WebAuthn Intent Compiler

**Branch:** `feat/webauthn-intent-compiler`
**Date:** 2026-08-26
**Scope:** Days 4–9 patches only. Days 1–3 are unchanged.

---

## Overview of the architectural change

The original plan uses a Discord DM + OTP modal as the human approval mechanism. The agent initiates checkout, the human receives a DM with cart details, types a 6-digit code, and the server issues a signed mandate.

We are replacing this with a **Cryptographic Intent Policy** system using WebAuthn (Passkeys/FaceID/TouchID). Instead of approving each cart individually, the human signs a policy document (max_spend, allowed_tags, merchant_id) using their device's authenticator. The AI agent then shops autonomously. When the agent calls `checkout_confirm`, the merchant server's **Intent Compiler** mathematically verifies the agent's cart against the human's signed policy.

**What stays the same across all days:**
- Razorpay integration (order creation, payment links, webhooks)
- Idempotency (IdempotencyRecord, persist-before-return)
- A2A merchant reasoning agent (cross_sell, finance_qa, campaign_draft)
- LangGraph buyer agent state machine (classify → search → consult → summarise → confirm)
- OAuth 2.1 AS, PKCE, MCP tools, spend caps, rate limits, audit trail, trace system
- `checkout_initiate` (recomputes total, freezes snapshot, checks caps — never trusts agent-supplied values)

**What changes:**
- `checkout_confirm` gains an **Intent Compiler** verification step that mathematically checks the cart against the signed policy before creating an order
- The human signs a policy once via WebAuthn instead of approving each cart via OTP
- The approval wait state is removed from the checkout flow; the agent shops freely within policy bounds
- The old OTP + mandate path is preserved as a commented-out fallback, clearly marked

**New dependency:** `fido2` (WebAuthn server library)

---

# Day 4 — The Intent Compiler, WebAuthn, Razorpay, Webhooks

**Date: Saturday, August 29, 2026.**

**This is the highest-risk day in the entire project.** Protect it: block out the full day, minimize interruptions, and don't let yourself get pulled into polishing yesterday's work instead of starting this. Everything that makes OpenStore's thesis true — the signed intent policy, the Intent Compiler, the spend cap, the idempotent retry — gets built today. If something has to slip, it should be tomorrow's buyer-agent polish, not this.

## What you'll have by tonight

A complete, curl-driven, end-to-end money flow using the Intent Compiler: a human signs a Cryptographic Intent Policy via WebAuthn (passkey), the agent shops autonomously, `checkout_confirm` runs the Intent Compiler which mathematically verifies the cart against the signed policy, and if compliant, creates a real Razorpay test-mode payment link — with zero polling anywhere in that chain, and zero human approval needed per-transaction.

---

## Changes Made

1. **New Concept §8**: "WebAuthn and Passkeys — asymmetric authentication without passwords"
2. **New Concept §9**: "The Intent Compiler — mathematical policy verification replaces human approval"
3. **Concept §1 (checkout state machine) updated**: removes `AWAITING_APPROVAL` state from the primary flow
4. **Concept §2 (OTP) preserved** but marked as the fallback path, not the primary flow
5. **New Step 2.5**: Intent Policy Pydantic models (`IntentPolicy`, `SignedIntentPolicy`, `PolicyChallenge`)
6. **New Step 4.5**: Intent Policy signing ceremony HTML page + WebAuthn JavaScript client
7. **New Step 4.6**: WebAuthn challenge/registration/verification FastAPI routes
8. **New Step 4.7**: `verify_cart_against_policy()` — the pure Intent Compiler function
9. **Step 6 (`checkout_confirm`) updated**: Intent Compiler runs as gate before Razorpay; old OTP/mandate path preserved as fallback
10. **Step 3 (`checkout_initiate`) updated**: no longer issues OTP or waits for approval; returns immediately after snapshot freeze

## Patch/Append Content

### Replace the "What you'll have by tonight" section

Replace the existing "What you'll have by tonight" paragraph with:

```
A complete, curl-driven, end-to-end money flow using the Intent Compiler: a human signs a
Cryptographic Intent Policy via WebAuthn (passkey) at a signing ceremony page, the agent
shops autonomously, `checkout_confirm` runs the Intent Compiler which mathematically verifies
the cart against the signed policy, and if compliant, creates a real Razorpay test-mode
payment link — with zero polling anywhere in that chain, and zero per-transaction human
approval needed. The human's single WebAuthn signature bounds everything the agent can do.
```

### Append new Concepts §8 and §9 (after existing Concept §7, before `---` and `## Build`)

```markdown
### 8. WebAuthn and Passkeys — asymmetric authentication without passwords

**WebAuthn** (Web Authentication, a W3C standard) is a protocol that lets a user authenticate to a server using a **passkey** — a cryptographic credential stored on their device (laptop, phone, security key) and activated by a biometric (FaceID, TouchID, fingerprint) or a PIN. It's the technology behind "Sign in with FaceID" or "Sign in with TouchID" on modern websites, and it replaces passwords entirely — no shared secret is ever transmitted.

The protocol works in two phases:

**Registration** — the server generates a random **challenge** (a 32-byte nonce), sends it to the browser along with the user's identity (a username, or in our case, a policy hash). The browser passes this to the **authenticator** (the device's biometric hardware), which generates a new asymmetric key pair. The **private key** never leaves the device; the **public key** (plus a credential ID) is sent back to the server and stored. The server now has everything it needs to verify future assertions from this credential.

**Authentication** (what WebAuthn calls "assertion") — the server generates another challenge, sends it to the browser. The browser asks the authenticator to sign the challenge with the stored private key (after the user performs their biometric). The authenticator returns a **signed assertion**: the challenge, the credential ID, a counter (to detect cloned authenticators), and a **signature** over the challenge plus authenticator metadata. The server verifies the signature against the stored public key.

The critical property for OpenStore: **the challenge is signed by the human's device, not by software.** A prompt-injected AI agent cannot produce a valid WebAuthn assertion because it does not have access to the private key — the key is physically inside the human's device, gated by their fingerprint or face. This is the same property that makes passkeys phishing-resistant for login, and we're repurposing it to make intent policies unforgeable.

```bash
uv add fido2
```

`fido2` is the Python server-side library for WebAuthn. It handles challenge generation, registration response verification, and assertion response verification — the cryptographic heavy lifting — so your server code stays at the level of "generate challenge, verify response, store credential."

### 9. The Intent Compiler — mathematical policy verification replaces human approval

The original plan uses a per-transaction OTP: the human approves each cart individually. This works, but it means the human must be online and attentive for every purchase — a scalability bottleneck that defeats the purpose of an autonomous AI buyer.

The **Intent Compiler** replaces per-transaction human approval with a single, upfront, cryptographically signed **Intent Policy** — a document that declares the human's constraints in machine-readable form:

```
max_spend_minor: 50000          (never spend more than ₹500 total)
allowed_tags: ["vegan", "dairy-free"]  (only buy items with these tags)
merchant_id: "gelateria-roma"   (only from this merchant)
```

The human signs this policy using WebAuthn (Concept §8 above). The signature proves the human physically interacted with their device to authorize these specific constraints — a prompt injection cannot forge it.

When the agent calls `checkout_confirm`, the server runs the **Intent Compiler**: a pure function that mathematically verifies every item in the agent's cart against the signed policy. No human approval is needed at checkout time — the policy already encodes the human's intent. If the cart violates the policy (e.g., a prompt injection added non-vegan items when the policy says `allowed_tags: ["vegan"]`), the compiler **hard-rejects** the checkout. If the cart complies, the checkout proceeds directly to Razorpay.

This is the architectural primitive that matches what AP2, Mastercard Verifiable Intent, and Visa TAP describe: a cryptographically bound, machine-enforceable human intent that an agent cannot exceed. The novelty here is implementing it with WebAuthn — the same authenticator billions of devices already support — rather than a bespoke signing scheme.

**The policy is the new approval.** There is no second step where a human clicks "Approve" on a per-cart basis. The human's single WebAuthn signature at the signing ceremony bounds everything the agent can do, forever (until the policy expires or the human revokes it). This is a stronger guarantee than OTP: the policy is a mathematical constraint, not a human who might click "Approve" on a poisoned embed without reading carefully.
```

### Update Concept §1 (checkout state machine) — replace the diagram

Replace the existing checkout state machine diagram with:

```markdown
### 1. The checkout state machine, and why it's drawn as a diagram at all

```
PENDING ──initiate──> POLICY_VERIFIED ──confirm──> ORDER_CREATED
   │                        │                           │
   └── expired/over-cap ────┴── policy reject ──────> REJECTED     webhook ▼
                                                                   PAID / FAILED
```

A **state machine** is a system that's always in exactly one of a fixed set of named states, and can only move between states along explicitly defined transitions. The value of drawing this _before_ writing code is that it makes illegal transitions visible as things that are simply absent from the diagram — there is no arrow from `PENDING` directly to `ORDER_CREATED`, which means your code should have no path that skips straight there either.

The Intent Compiler changes this diagram from the original plan: there is no `AWAITING_APPROVAL` state. The human signs a policy once (before the agent starts shopping), and the checkout proceeds autonomously through `POLICY_VERIFIED` → `ORDER_CREATED`. The security check happens mathematically inside `checkout_confirm`, not through a human-in-the-loop pause.

**Fallback path preserved:** the original OTP + mandate flow is still available. If you need to demo the old-style per-transaction approval, uncomment the OTP issuance code in `checkout_initiate` (Step 3) and the `AWAITING_APPROVAL` → `MANDATE_ISSUED` transitions. The Intent Compiler path is the primary flow for this project's thesis; the OTP path exists for backward-compatibility demos only.
```

### Update Concept §2 — mark OTP as fallback

Add this paragraph at the end of the existing OTP concept section:

```markdown
**In the Intent Compiler flow, OTP is not used.** The human signs a policy once via WebAuthn, and the Intent Compiler verifies carts mathematically at checkout time. The OTP path is preserved as a fallback for backward-compatibility demos — you can uncomment the OTP issuance in `checkout_initiate` and restore the `AWAITING_APPROVAL` state if you need to show the original per-transaction approval flow. For the primary demo, the Intent Compiler path is what you show.
```

### Append new Step 2.5 — Intent Policy models (after existing Step 2, before Step 3)

```markdown
### Step 2.5 — Intent Policy models

**What**: define the data structures for the Cryptographic Intent Policy — the document the human signs via WebAuthn, and the server-side structures for storing credentials and challenges. **Tool**: Pydantic `BaseModel` and `SQLModel`. **Why**: these models are the contract between the signing ceremony (Step 4.5), the WebAuthn verification (Step 4.6), and the Intent Compiler (Step 4.7). Freezing them now means all three steps build against the same shape.

Add to `merchant/models.py`:

```python
class IntentPolicy(BaseModel):
    """The machine-readable policy a human signs via WebAuthn."""
    max_spend_minor: int              # maximum total for any single checkout
    allowed_tags: list[str]           # only items with at least one of these tags are permitted
    blocked_skus: list[str] = []      # explicitly forbidden items (belt-and-suspenders)
    merchant_id: str                  # lock to a specific merchant
    expires_at: int                   # Unix timestamp; policy is invalid after this

class SignedIntentPolicy(BaseModel):
    """A policy with its WebAuthn binding."""
    policy: IntentPolicy
    credential_id: str                # base64url-encoded WebAuthn credential ID
    signature: str                    # base64url-encoded WebAuthn assertion signature
    authenticator_data: str           # base64url-encoded authenticatorData
    client_data_json: str             # base64url-encoded clientDataJSON
```

Add the `IntentPolicyRow` table model (stores registered credentials and their policies):

```python
class IntentPolicyRow(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    credential_id: str = Field(unique=True, index=True)   # base64url-encoded
    public_key: str                                         # base64url-encoded COSE public key
    sign_count: int = Field(default=0)
    policy_json: dict = Field(default_factory=dict, sa_column=Column(JSON))  # IntentPolicy as dict
    user_id: str = Field(default="default-user", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    active: bool = Field(default=True)
```

Add the `PolicyChallenge` table model (temporary challenges for WebAuthn ceremonies, TTL-bound):

```python
class PolicyChallenge(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    challenge_id: str = Field(unique=True, index=True)     # base64url-encoded challenge bytes
    checkout_id: str                                         # bound to a specific checkout
    policy_hash: str                                         # SHA-256 of the canonical policy JSON
    expires_at: datetime
    used: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

### Append new Step 4.5 — Intent Policy signing ceremony page

**What**: a self-contained HTML page served by the merchant server where the human sets their policy constraints and signs with their passkey. **Tool**: HTML + vanilla JavaScript using the browser's `navigator.credentials` WebAuthn API. **Why**: this is the human-facing interface for the Intent Compiler — the single point where the human authorizes the policy. It must be served by the merchant server (same origin as the WebAuthn API calls) for the ceremony to work.

`merchant/intent_routes.py`:

```python
from fastapi import APIRouter, Depends, HTTPException, Body
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select
import hashlib
import secrets
import time
import uuid

from merchant.db import get_session
from merchant.models import IntentPolicyRow, PolicyChallenge, IntentPolicy
from merchant.mandate import canonical_json_bytes
from fido2.server import Fido2Server
from fido2.webauthn import PublicKeyCredentialRpEntity, PublicKeyCredentialUserEntity

rp = PublicKeyCredentialRpEntity(id="localhost", name="OpenStore Merchant")
fido_server = Fido2Server(rp)

intent_router = APIRouter()
```

Serve the signing ceremony HTML page:

```python
@intent_router.get("/intent/sign", response_class=HTMLResponse)
def intent_signing_page(request: Request):
    """Serves the Intent Policy signing ceremony page."""
    return templates.TemplateResponse(request, "intent_sign.html")
```

`merchant/storefront/intent_sign.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Sign Intent Policy — OpenStore</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 600px; margin: 2rem auto; padding: 0 1rem; }
    label { display: block; margin: 1rem 0 0.25rem; font-weight: 600; }
    input, select { width: 100%; padding: 0.5rem; font-size: 1rem; box-sizing: border-box; }
    button { margin-top: 1.5rem; padding: 0.75rem 2rem; font-size: 1rem; cursor: pointer;
             background: #2563eb; color: white; border: none; border-radius: 6px; }
    button:hover { background: #1d4ed8; }
    button:disabled { background: #94a3b8; cursor: not-allowed; }
    #status { margin-top: 1rem; padding: 1rem; border-radius: 6px; display: none; }
    #status.ok { display: block; background: #dcfce7; color: #166534; }
    #status.err { display: block; background: #fee2e2; color: #991b1b; }
  </style>
</head>
<body>
  <h1>Sign Your Intent Policy</h1>
  <p>Set the constraints your AI agent must follow. This policy is cryptographically
     bound to your passkey — once signed, the merchant server will enforce these limits
     mathematically on every checkout.</p>

  <form id="policyForm">
    <label for="max_spend">Maximum spend per checkout (₹)</label>
    <input type="number" id="max_spend" value="500" min="1" max="100000">

    <label for="allowed_tags">Allowed product tags (comma-separated)</label>
    <input type="text" id="allowed_tags" value="vegan, dairy-free, fruit"
           placeholder="e.g. vegan, dairy-free, gift">

    <label for="merchant_id">Merchant ID</label>
    <input type="text" id="merchant_id" value="gelateria-roma">

    <label for="expiry_hours">Policy expires after (hours)</label>
    <input type="number" id="expiry_hours" value="24" min="1" max="168">

    <button type="submit" id="signBtn">Sign with Passkey</button>
  </form>

  <div id="status"></div>

  <script>
    const MERCHANT_URL = window.location.origin;

    document.getElementById("policyForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = document.getElementById("signBtn");
      const status = document.getElementById("status");
      btn.disabled = true;
      status.className = ""; status.style.display = "none";

      try {
        const policy = {
          max_spend_minor: parseInt(document.getElementById("max_spend").value) * 100,
          allowed_tags: document.getElementById("allowed_tags").value
            .split(",").map(t => t.trim()).filter(Boolean),
          blocked_skus: [],
          merchant_id: document.getElementById("merchant_id").value,
          expires_at: Math.floor(Date.now()/1000)
            + parseInt(document.getElementById("expiry_hours").value) * 3600,
        };

        // Step 1: Get challenge from server (contains policy hash)
        const challengeResp = await fetch(`${MERCHANT_URL}/internal/webauthn/challenge`, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({checkout_id: "policy-signing", policy: policy}),
        });
        if (!challengeResp.ok) throw new Error("Failed to get challenge");
        const {challenge, challenge_id} = await challengeResp.json();

        // Step 2: Create WebAuthn credential (registration + assertion in one ceremony)
        const challengeBytes = Uint8Array.from(atob(challenge), c => c.charCodeAt(0));

        const createOptions = {
          publicKey: {
            challenge: challengeBytes,
            rp: {name: "OpenStore Merchant", id: "localhost"},
            user: {
              id: new Uint8Array(16),
              name: "openstore-user",
              displayName: "OpenStore User",
            },
            pubKeyCredParams: [
              {alg: -7, type: "public-key"},   // ES256
              {alg: -257, type: "public-key"},  // RS256
            ],
            authenticatorSelection: {
              authenticatorAttachment: "platform",
              userVerification: "required",
            },
            timeout: 60000,
          },
        };

        const credential = await navigator.credentials.create(createOptions);

        // Step 3: Register credential + policy on server
        const regResp = await fetch(`${MERCHANT_URL}/internal/webauthn/register`, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            credential: {
              id: credential.id,
              rawId: btoa(String.fromCharCode(...new Uint8Array(credential.rawId))),
              type: credential.type,
              response: {
                attestationObject: btoa(String.fromCharCode(
                  ...new Uint8Array(credential.response.attestationObject))),
                clientDataJSON: btoa(String.fromCharCode(
                  ...new Uint8Array(credential.response.clientDataJSON))),
              },
            },
            challenge_id: challenge_id,
            policy: policy,
          }),
        });

        if (!regResp.ok) {
          const err = await regResp.json();
          throw new Error(err.detail || "Registration failed");
        }

        status.className = "ok";
        status.textContent = "Intent Policy signed and registered. Your agent may now shop.";
      } catch (err) {
        status.className = "err";
        status.textContent = `Error: ${err.message}`;
      } finally {
        btn.disabled = false;
      }
    });
  </script>
</body>
</html>
```

**How the ceremony binds the policy to the signature:** the server hashes the canonical JSON of the policy and uses that hash as the WebAuthn challenge. The browser sends this challenge to the authenticator (your device's biometric hardware), which signs it with the private key. The signature therefore covers the policy hash — proving the human physically authorized _this specific policy_ (not some other policy, not a blank check). The server verifies the WebAuthn assertion (confirming the signature is valid and from a registered credential) and simultaneously confirms the challenge matches the stored policy hash. Two independent checks, both must pass.

### Append new Step 4.6 — WebAuthn challenge/registration/verification routes

**What**: the server-side FastAPI routes that drive the WebAuthn ceremony: issuing challenges, registering credentials, and verifying assertions. **Tool**: `fido2` library for cryptographic verification, `hashlib` for policy hashing. **Why**: these routes are the "compiler" in "Intent Compiler" — they take the human's signed policy and store it in a form the checkout path can verify against.

Add to `merchant/intent_routes.py`:

```python
@intent_router.post("/internal/webauthn/challenge")
def issue_webauthn_challenge(
    checkout_id: str = Body(...),
    policy: dict = Body(...),
    session: Session = Depends(get_session),
):
    """Generate a WebAuthn challenge whose bytes are the SHA-256 hash of the
    canonical policy JSON. The authenticator will sign these bytes, binding
    the human's biometric to this exact policy."""
    intent_policy = IntentPolicy(**policy)

    # Canonicalize the policy and hash it — this becomes the challenge
    policy_canonical = canonical_json_bytes(intent_policy.model_dump())
    policy_hash = hashlib.sha256(policy_canonical).hexdigest()
    challenge_bytes = bytes.fromhex(policy_hash)  # 32 bytes, valid WebAuthn challenge

    challenge_b64 = base64.urlsafe_b64encode(challenge_bytes).rstrip(b"=").decode()

    session.add(PolicyChallenge(
        challenge_id=challenge_b64,
        checkout_id=checkout_id,
        policy_hash=policy_hash,
        expires_at=datetime.utcnow() + timedelta(minutes=10),
    ))
    session.commit()

    return {"challenge": challenge_b64, "challenge_id": challenge_b64}


@intent_router.post("/internal/webauthn/register")
def register_webauthn_credential(
    credential: dict = Body(...),
    challenge_id: str = Body(...),
    policy: dict = Body(...),
    session: Session = Depends(get_session),
):
    """Verify the WebAuthn registration response, store the credential,
    and bind it to the signed Intent Policy."""
    challenge_row = session.exec(
        select(PolicyChallenge).where(PolicyChallenge.challenge_id == challenge_id)
    ).first()
    if challenge_row is None or challenge_row.used or challenge_row.expires_at < datetime.utcnow():
        raise HTTPException(400, "Invalid or expired challenge")

    # Verify the attestation — confirms the credential was freshly created
    # and the challenge was signed by a genuine authenticator
    try:
        auth_data = fido_server.register_complete(
            challenge_row.challenge_id,
            credential,
        )
    except Exception as e:
        raise HTTPException(400, f"WebAuthn registration failed: {e}")

    challenge_row.used = True
    session.add(challenge_row)

    # Store credential + policy
    cred_id_b64 = base64.urlsafe_b64encode(auth_data.credential_data.credential_id).rstrip(b"=").decode()
    pk_bytes = auth_data.credential_data.public_key
    pk_b64 = base64.urlsafe_b64encode(pk_bytes).rstrip(b"=").decode()

    session.add(IntentPolicyRow(
        credential_id=cred_id_b64,
        public_key=pk_b64,
        sign_count=auth_data.sign_count,
        policy_json=policy,
        active=True,
    ))
    session.commit()

    return {"status": "registered"}


@intent_router.post("/internal/webauthn/verify")
def verify_webauthn_assertion(
    checkout_id: str = Body(...),
    assertion: dict = Body(...),
    session: Session = Depends(get_session),
):
    """Verify a WebAuthn assertion against the stored policy for this checkout.
    Returns the policy if valid; raises 403 if the assertion is invalid or the
    policy does not cover this checkout."""
    # Find the registered credential
    cred_id_b64 = assertion.get("id", "")
    row = session.exec(
        select(IntentPolicyRow)
        .where(IntentPolicyRow.credential_id == cred_id_b64)
        .where(IntentPolicyRow.active == True)
    ).first()
    if row is None:
        raise HTTPException(403, "Unknown credential — policy not registered")

    # Recompute the expected challenge from the stored policy
    intent_policy = IntentPolicy(**row.policy_json)
    policy_canonical = canonical_json_bytes(intent_policy.model_dump())
    expected_challenge = hashlib.sha256(policy_canonical).hexdigest()

    # Verify the assertion signature — confirms the human's biometric
    # was used to sign this exact policy hash
    try:
        fido_server.authenticate_complete(
            row.sign_count,
            {"id": row.credential_id, "public_key": row.public_key},
            expected_challenge,
            assertion,
        )
    except Exception as e:
        raise HTTPException(403, f"WebAuthn assertion invalid: {e}")

    # Update sign count (replay detection — a cloned authenticator
    # would have a lower counter)
    row.sign_count = row.sign_count + 1
    session.add(row)
    session.commit()

    return {"policy": row.policy_json}
```

### Append new Step 4.7 — `verify_cart_against_policy()`: the pure Intent Compiler function

**What**: the mathematical verification function that checks a cart against a signed Intent Policy. **Tool**: pure Python — no LLM, no external calls, no ambiguity. **Why**: this is the core of the "Intent Compiler" claim. It must be a pure function: given a cart and a policy, return pass or fail with a specific reason. No agent can influence its output; no prompt injection can change its behavior.

Add to `merchant/intent_compiler.py`:

```python
from merchant.models import IntentPolicy
from merchant.trace import emit
import uuid


def verify_cart_against_policy(
    cart_items: list[dict],
    policy: dict,
    merchant_id: str,
    product_lookup: callable,
) -> tuple[bool, str]:
    """The Intent Compiler: mathematically verify a cart against a signed policy.

    Args:
        cart_items: list of {"sku": str, "qty": int, "unit_minor": int}
        policy: the IntentPolicy dict from the signed WebAuthn credential
        merchant_id: the merchant attempting checkout
        product_lookup: callable(sku) -> Product | None, for tag lookups

    Returns:
        (True, "ok") if the cart complies with the policy,
        (False, reason_string) if any item violates the policy.
    """
    intent = IntentPolicy(**policy)
    trace_id = str(uuid.uuid4())

    # 1. Merchant check
    if intent.merchant_id != merchant_id:
        emit("merchant-server", "Intent Compiler: wrong merchant",
             {"expected": intent.merchant_id, "got": merchant_id},
             trace_id, "blocked")
        return False, f"Policy bound to merchant '{intent.merchant_id}', not '{merchant_id}'"

    # 2. Expiry check
    import time
    if intent.expires_at < time.time():
        emit("merchant-server", "Intent Compiler: policy expired",
             {"expires_at": intent.expires_at}, trace_id, "blocked")
        return False, "Intent policy has expired"

    # 3. Per-item checks
    for item in cart_items:
        sku = item["sku"]

        # 3a. Blocked SKU check
        if sku in intent.blocked_skus:
            emit("merchant-server", "Intent Compiler: blocked SKU",
                 {"sku": sku}, trace_id, "blocked")
            return False, f"Item '{sku}' is explicitly blocked by policy"

        # 3b. Tag check — item must have at least one allowed tag
        product = product_lookup(sku)
        if product is None:
            emit("merchant-server", "Intent Compiler: unknown SKU",
                 {"sku": sku}, trace_id, "blocked")
            return False, f"Unknown product '{sku}'"

        if intent.allowed_tags:
            item_tags = set(product.tags or [])
            policy_tags = set(intent.allowed_tags)
            if not item_tags.intersection(policy_tags):
                emit("merchant-server", "Intent Compiler: tag violation",
                     {"sku": sku, "item_tags": list(item_tags),
                      "allowed": list(policy_tags)},
                     trace_id, "blocked")
                return False, (
                    f"Item '{sku}' has tags {list(item_tags)} but policy "
                    f"only allows {list(policy_tags)}"
                )

    # 4. Total spend check
    total = sum(item["unit_minor"] * item["qty"] for item in cart_items)
    if total > intent.max_spend_minor:
        emit("merchant-server", "Intent Compiler: over spend limit",
             {"total": total, "limit": intent.max_spend_minor},
             trace_id, "blocked")
        return False, (
            f"Cart total ₹{total/100:.2f} exceeds policy limit "
            f"₹{intent.max_spend_minor/100:.2f}"
        )

    emit("merchant-server", "Intent Compiler: cart complies with policy",
         {"total": total, "items": len(cart_items)}, trace_id, "info")
    return True, "ok"
```

This function is intentionally **pure**: it takes a cart and a policy, and returns a boolean + reason. It makes no database calls, no LLM calls, no external HTTP calls. It cannot be influenced by prompt injection because it never sees LLM output — it only sees the cart (recomputed from the database) and the policy (cryptographically signed by the human). The `product_lookup` callable is the only dependency, and it reads from the same catalog adapter the rest of the server uses — not from anything the agent supplied.

### Update Step 3 (`checkout_initiate`) — replace the OTP/approval flow

Replace the existing `checkout_initiate` function with this version. The key change: no OTP is issued, no `AWAITING_APPROVAL` state, no `send_approval_dm`. The checkout returns immediately after the snapshot is frozen, and the agent proceeds directly to `checkout_confirm`.

```python
import hashlib
import uuid
from datetime import datetime, timedelta
from merchant.models import Cart, Checkout
from merchant.policy import rolling_spend_minor

PER_TX_CAP_MINOR = 50000
ROLLING_CAP_MINOR = 200000

def compute_cart_hash(items: list[dict]) -> str:
    return hashlib.sha256(canonical_json_bytes({"items": items})).hexdigest()

@mcp.tool()
@audited_tool("checkout_initiate")
def checkout_initiate(cart_id: int, delivery_address: str, ctx: Context, trace_id: str = None) -> dict:
    """Initiates checkout for a cart. Recomputes the total from the DB,
    checks spend caps, freezes an immutable snapshot. In the Intent Compiler
    flow, no OTP is issued — the agent proceeds directly to checkout_confirm,
    where the Intent Compiler verifies the cart against the signed policy."""
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
            status="POLICY_VERIFIED",  # Intent Compiler flow: no approval wait
            cart_hash=cart_hash,
            total_minor=total_minor,
            delivery_address=delivery_address,
            expires_at=datetime.utcnow() + timedelta(minutes=5),
        )
        session.add(checkout)
        session.commit()

        emit("merchant-server", "Checkout initiated (Intent Compiler path)",
             {"checkout_id": checkout_id, "total": total_minor}, trace_id, "gate")
        return {
            "checkout_id": checkout_id,
            "status": "POLICY_VERIFIED",
            "expires_in_seconds": 300,
            "next_step": "checkout_confirm — the Intent Compiler will verify your cart against the signed policy",
        }
```

### Update Step 6 (`checkout_confirm`) — add Intent Compiler gate

Add the Intent Compiler verification as the first check in `checkout_confirm`, before any Razorpay call. The old OTP/mandate path is preserved as a commented-out fallback.

```python
from merchant.intent_compiler import verify_cart_against_policy
from merchant.catalog.yaml_adapter import YAMLCatalogAdapter
from merchant.config import settings

_intent_catalog = YAMLCatalogAdapter(settings.merchant_config_path)

@mcp.tool()
@audited_tool("checkout_confirm")
def checkout_confirm(
    jws: str = None,               # optional: for OTP/mandate fallback path
    intent_checkout_id: str = None, # for Intent Compiler path
    idempotency_key: str = None,
    ctx: Context = None,
    trace_id: str = None,
) -> dict:
    """Confirms a checkout. In the Intent Compiler path, the cart is
    verified against the human's signed WebAuthn policy before any
    Razorpay call. In the fallback OTP path, a signed mandate is used
    instead (same verification ladder as the original plan)."""
    claims = get_claims_from_context(ctx, "checkout:confirm")
    if not check_rate_limit(claims["sub"], "checkout_confirm"):
        raise HTTPException(429, "Rate limit exceeded")

    with Session(engine) as session:
        # Idempotency check (same as original)
        existing = session.exec(
            select(IdempotencyRecord)
            .where(IdempotencyRecord.client_id == claims["sub"])
            .where(IdempotencyRecord.idempotency_key == idempotency_key)
        ).first()
        if existing is not None:
            emit("merchant-server", "Idempotent replay", {}, trace_id, "info")
            return existing.response_json

        # === PATH A: Intent Compiler (primary) ===
        if intent_checkout_id is not None:
            checkout = session.exec(
                select(Checkout).where(Checkout.checkout_id == intent_checkout_id)
            ).first()
            if checkout is None or checkout.status != "POLICY_VERIFIED":
                raise HTTPException(400, "Checkout not in POLICY_VERIFIED state")

            # Get the active signed policy for this client
            policy_row = session.exec(
                select(IntentPolicyRow)
                .where(IntentPolicyRow.active == True)
                .order_by(IntentPolicyRow.created_at.desc())
            ).first()
            if policy_row is None:
                raise HTTPException(403, "No signed Intent Policy found — sign a policy first")

            # Load the cart from DB (never trust agent-supplied data)
            cart = session.get(Cart, checkout.cart_id)
            if cart is None:
                raise HTTPException(400, "Cart not found")

            # Run the Intent Compiler — mathematical verification
            ok, reason = verify_cart_against_policy(
                cart_items=cart.items_json,
                policy=policy_row.policy_json,
                merchant_id="gelateria-roma",
                product_lookup=_intent_catalog.get_product,
            )
            if not ok:
                emit("merchant-server", "Intent Compiler REJECTED checkout",
                     {"reason": reason, "checkout_id": intent_checkout_id},
                     trace_id, "blocked")
                checkout.status = "REJECTED"
                session.add(checkout)
                session.commit()
                raise HTTPException(403, f"Intent Compiler rejected: {reason}")

            # Policy verified — proceed to Razorpay
            # Re-check spend cap (state may have changed since initiate)
            already_spent = rolling_spend_minor(session, claims["sub"])
            if already_spent + checkout.total_minor > ROLLING_CAP_MINOR:
                raise HTTPException(400, "Rolling spend cap exceeded at confirm time")

            # Idempotency key required for Intent Compiler path
            if idempotency_key is None:
                raise HTTPException(400, "idempotency_key required")

            if INJECT_TIMEOUT["enabled"]:
                rp_result = create_order_and_payment_link(checkout.total_minor, checkout.checkout_id)
                _persist_confirm_result(session, checkout, claims["sub"], idempotency_key, rp_result, trace_id)
                INJECT_TIMEOUT["enabled"] = False
                raise HTTPException(504, "Simulated timeout — response dropped after order creation")

            rp_result = create_order_and_payment_link(checkout.total_minor, checkout.checkout_id)
            result = _persist_confirm_result(session, checkout, claims["sub"], idempotency_key, rp_result, trace_id)
            return result

        # === PATH B: OTP + Mandate (fallback, same as original plan) ===
        # Uncomment this block to use the original per-transaction approval flow
        #
        # if jws is not None:
        #     ... (original Step 6 verification ladder unchanged) ...
        #
        raise HTTPException(400, "Provide intent_checkout_id (Intent Compiler) or jws (mandate fallback)")
```

Note the two paths: **Path A** (Intent Compiler, primary) uses `intent_checkout_id` and verifies the cart against the signed policy. **Path B** (OTP + Mandate, fallback) uses `jws` and runs the original seven-step verification ladder from the plan. Both paths share the same idempotency check and the same Razorpay integration — the only difference is _how_ the human's authorization is verified.

### Append "Running the full end-to-end flow" — new section

Add this section after Step 7 (webhooks), replacing the existing "Running the full end-to-end flow" section:

```markdown
## Running the full end-to-end flow (Intent Compiler path)

Start the merchant server. Then:

**Step 1 — Sign the Intent Policy:**
Open `http://localhost:8000/intent/sign` in your browser. Set the policy constraints
(e.g., max spend ₹500, allowed tags `vegan, dairy-free`, merchant `gelateria-roma`).
Click "Sign with Passkey" and complete the biometric. You should see "Intent Policy
signed and registered."

**Step 2 — Create a cart and checkout:**
Using a valid `checkout:initiate` + `checkout:confirm`-scoped token:
```bash
# create a cart (via MCP tool), then:
curl -X POST http://localhost:8000/agent/mcp \
  -H "Authorization: Bearer <token>" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"checkout_initiate","arguments":{"cart_id": 1, "delivery_address": "221B Baker Street"}}}'
```

**Step 3 — Confirm with Intent Compiler:**
```bash
curl -X POST http://localhost:8000/agent/mcp \
  -H "Authorization: Bearer <token>" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"checkout_confirm","arguments":{"intent_checkout_id": "<checkout_id_from_step_2>", "idempotency_key": "test-key-1"}}}'
```

If the cart complies with the policy, you get back a `payment_link_url`. If not,
you get `403 Intent Compiler rejected: <reason>` — for example, "Item 'gel-005' has
tags ['coffee', 'gift'] but policy only allows ['vegan', 'dairy-free', 'fruit']".

**Step 4 — Pay and verify webhook:**
Open the payment link, pay with Razorpay test card details. The webhook flips the
order to `PAID`. Check `/admin/audit` for the full trail.
```

### Update "Exit check" section

Replace with:

```markdown
## Exit check (from the plan)

> ✅ Exit: curl-driven end-to-end — sign policy → cart → initiate → confirm (Intent Compiler verifies) → real Razorpay test payment link → pay it → webhook flips the order to PAID.

Verify the Intent Compiler rejection path too: add a non-vegan item (e.g., `gel-005`
Tiramisu, tags: `coffee, gift`) to a cart, initiate checkout, confirm — the Intent
Compiler should reject with a clear tag-violation reason. Check that the rejection
appears in `#merchant-server` with `level="blocked"` and in `/admin/audit` with
`success=False`.
```

### Update "What could go wrong" section — append WebAuthn-specific entries

Append these entries to the existing "What could go wrong" list:

```markdown
- **WebAuthn ceremony fails in the browser ("NotAllowedError")**: WebAuthn requires
  HTTPS or localhost. If you're accessing the signing page from a different hostname
  (e.g., via the cloudflared tunnel), the browser blocks the ceremony. Access the
  signing page directly at `http://localhost:8000/intent/sign`, not through the tunnel.
- **"challenge mismatch" error during WebAuthn verify**: the policy hash must be
  computed identically at challenge-issuance time and verification time. Confirm both
  paths call `canonical_json_bytes(intent_policy.model_dump())` with the same
  `IntentPolicy` fields — a missing field or different default value will produce a
  different hash. Debug by printing both hashes and comparing.
- **`fido2` throws "Invalid challenge length"**: WebAuthn challenges must be 32–64
  bytes. SHA-256 produces exactly 32 bytes, which is valid. If you're hashing with
  something other than SHA-256, check the output length.
- **Policy stored but `verify_cart_against_policy` says "No signed Intent Policy found"**:
  the `IntentPolicyRow` was not committed, or `active=False`. Check the database
  directly: `SELECT * FROM intentpolicyrow;`.
- **Intent Compiler rejects a compliant cart**: most likely a tag mismatch — confirm
  the tags in your `gelateria.yaml` exactly match what you typed in the signing page
  (case-sensitive, no trailing spaces). `gel-003` Mango Sorbetto has tags
  `["fruit", "dairy-free", "vegan"]`; if your policy says `["vegan"]`, the
  intersection is non-empty and it should pass.
```

---
```

---

# Day 5 — Buyer Agent + Discord

**Date: Sunday, August 30, 2026.**

From here on, the depth tapers the way Day 0 promised: FastAPI routes, SQLModel queries, trace emission, and Pydantic contracts are all patterns you've now built by hand multiple times, so they show up from here as **signatures only** — name, inputs, outputs, one line of behavior. Genuinely new concepts (today: LangGraph, `interrupt()`, checkpointing) still get the full treatment.

## What you'll have by tonight

A fully separate, isolated `buyer_agent/` package — one that imports _nothing_ from `merchant/` or `merchant_agent/` — that discovers your merchant purely from its `.well-known` document, does the full OAuth PKCE dance itself, and runs a LangGraph-driven conversation in a real Discord channel: search, cart, checkout-initiate, checkout-confirm (Intent Compiler verifies), and explain. Driven entirely from chat.

---

## Changes Made

1. **"What you'll have by tonight" updated**: removed "pause for human approval" language; replaced with "Intent Compiler verifies"
2. **Concept §3 (`interrupt()`)**: updated to note the Intent Compiler flow does not use `interrupt()` for approval; `interrupt()` is preserved for future extensions
3. **Step 3 (`graph.py`)**: `request_approval_node` removed from the primary graph; `confirm_node` now calls `checkout_confirm` with `intent_checkout_id` directly
4. **New Step 3.5**: `trigger_intent_signing()` — opens the browser to the signing ceremony page before the agent starts shopping
5. **Step 4 (`bot.py`)**: updated to trigger the intent signing ceremony on first use

## Patch/Append Content

### Update "What you'll have by tonight"

Replace with:

```
A fully separate, isolated `buyer_agent/` package — one that imports _nothing_ from
`merchant/` or `merchant_agent/` — that discovers your merchant purely from its
`.well-known` document, does the full OAuth PKCE dance itself, triggers the Intent
Policy signing ceremony (opening the browser to the merchant's signing page), and runs
a LangGraph-driven conversation in a real Discord channel: search, cart, checkout-initiate,
checkout-confirm (the Intent Compiler verifies the cart against the signed policy), and
explain. The agent never pauses for human approval at checkout — the signed policy
is the approval.
```

### Update Concept §3 (`interrupt()`) — add Intent Compiler note

Append this paragraph to the end of the existing `interrupt()` concept section:

```markdown
**In the Intent Compiler flow, `interrupt()` is not used for checkout approval.**
The graph proceeds directly from `checkout_initiate` to `checkout_confirm` without
pausing — the Intent Compiler verifies the cart mathematically, so no human-in-the-loop
pause is needed. `interrupt()` remains available for future extensions (e.g., a
"confirm this large purchase?" prompt for carts over a threshold), but the core
happy path does not use it. This is a deliberate simplification: fewer states =
fewer things that can get stuck during a live demo.
```

### Update Step 3 (`graph.py`) — remove approval pause, add direct confirm

Replace the node signatures section with:

```markdown
### Step 3 — `graph.py`: the LangGraph state machine

**What**: the actual graph — nodes and edges implementing the flow described in Concepts. In the Intent Compiler flow, the graph is simpler than the original plan: there is no approval-pause node. **Signatures for each node**:

```python
def classify_node(state: ConversationState) -> dict:
    """Given state['last_user_message'], decides intent: browse, add-to-cart,
    checkout, or general question. Returns {'intent': <str>}."""

def search_node(state: ConversationState) -> dict:
    """Calls the search_products MCP tool with a query derived from the
    user's message. Returns {'search_results': list[dict]}."""

def consult_merchant_agent_node(state: ConversationState) -> dict:
    """Calls the merchant reasoning agent over A2A for a cross-sell
    suggestion given the current cart. STUBBED today — returns a fixed
    placeholder; wired to the real A2A call on Day 6."""

def summarize_cart_node(state: ConversationState) -> dict:
    """Formats the current cart contents + total for display in Discord.
    Returns {'cart_summary': str}."""

def checkout_node(state: ConversationState) -> dict:
    """Calls checkout_initiate then checkout_confirm in sequence.
    checkout_confirm uses intent_checkout_id (Intent Compiler path) —
    the server verifies the cart against the signed policy. Returns
    {'order_result': dict}."""

def explain_node(state: ConversationState) -> dict:
    """Formats the final result (or rejection reason) into a human-readable
    message for the Discord reply. Returns {'reply_text': str}."""
```

Wire them with a **linear graph** — no conditional edges needed for the happy path:

```python
graph = StateGraph(ConversationState)
graph.add_node("classify", classify_node)
graph.add_node("search", search_node)
graph.add_node("consult", consult_merchant_agent_node)
graph.add_node("summarize", summarize_cart_node)
graph.add_node("checkout", checkout_node)
graph.add_node("explain", explain_node)

graph.add_edge("classify", "search")
graph.add_edge("search", "consult")
graph.add_edge("consult", "summarize")
graph.add_edge("summarize", "checkout")
graph.add_edge("checkout", "explain")
graph.add_edge("explain", END)

graph.set_entry_point("classify")
```

Compile with a `SqliteSaver` checkpointer (same as before — this is still needed for
demo resilience, even without `interrupt()`):

```python
from langgraph.checkpoint.sqlite import SqliteSaver

checkpointer = SqliteSaver.from_conn_string("buyer_agent_state.db")
compiled_graph = graph.compile(checkpointer=checkpointer)
```
```

### Append new Step 3.5 — Trigger the Intent Signing ceremony

**What**: before the agent starts shopping, ensure the human has signed an Intent Policy. **Tool**: Python's `webbrowser` module (same pattern as Day 2's PKCE consent). **Why**: the Intent Compiler cannot verify a cart without a signed policy — the agent must ensure the policy exists before attempting checkout.

```python
import webbrowser

def ensure_intent_policy_signed(merchant_url: str) -> bool:
    """Opens the merchant's Intent Policy signing ceremony in the browser.
    Returns True if a policy is already registered (checks via a simple
    GET to a status endpoint), False if the user needs to sign one."""
    # Check if a policy already exists
    resp = httpx.get(f"{merchant_url}/internal/intent-status")
    if resp.status_code == 200 and resp.json().get("has_policy"):
        return True

    # No policy yet — open the signing page
    webbrowser.open(f"{merchant_url}/intent/sign")
    return False
```

Add the status endpoint to `merchant/intent_routes.py`:

```python
@intent_router.get("/internal/intent-status")
def intent_status(session: Session = Depends(get_session)):
    """Check if any active Intent Policy exists."""
    row = session.exec(
        select(IntentPolicyRow).where(IntentPolicyRow.active == True)
    ).first()
    return {"has_policy": row is not None}
```

### Update Step 4 (`bot.py`) — trigger signing on first use

Add to the `on_message_handler` signature:

```markdown
### Step 4 — `bot.py`: the OpenStore Buyer Discord bot

**Signatures:**

```python
async def on_message_handler(message: discord.Message) -> None:
    """Registered against the #buyer-agent channel. On first message in a
    session, calls ensure_intent_policy_signed() to open the signing page.
    Then loads/creates the conversation's checkpoint, invokes the graph,
    and sends state['reply_text'] back to the channel."""

async def handle_checkout_result(result: dict) -> str:
    """Formats the Intent Compiler result: if checkout succeeded, shows
    the payment link; if rejected, shows the policy-violation reason
    (e.g., 'Item X has tags [Y] but your policy only allows [Z]').
    This rejection message is the human-readable output of the
    Intent Compiler — the moment they see their policy enforced."""
```
```

### Update "Exit check" section

Replace with:

```markdown
## Exit check (from the plan)

> ✅ Exit: full happy path driven entirely from Discord chat, with Intent Compiler verification. **Record this immediately** — it's your fallback demo asset.

Concretely: from a fresh Discord message in `#buyer-agent` (something like "I want a pistachio gelato"), the conversation should flow through search → cart → checkout-initiate → checkout-confirm (Intent Compiler verifies) → a final reply confirming the order. No approval pause, no DM, no OTP — the signed policy is the approval. The actual payment should complete on Razorpay's test dashboard by the end.

**Also test the rejection path today**: add a non-vegan item to the cart (e.g., Tiramisu, tags: `coffee, gift`) and confirm the agent gets a clean rejection message from the Intent Compiler — "Item 'gel-005' has tags ['coffee', 'gift'] but policy only allows ['vegan', 'dairy-free', 'fruit']". This rejection message is a key demo beat on Day 9.
```

### Update "What could go wrong" — append Intent Compiler entries

Append:

```markdown
- **Agent says "no policy found" at checkout**: the signing ceremony was not completed, or the browser session that signed the policy was different from the server's database. Open `http://localhost:8000/internal/intent-status` to check if a policy is registered; if not, re-run the signing ceremony.
- **Signing page opens but "Sign with Passkey" does nothing**: confirm you're accessing `http://localhost:8000/intent/sign` directly (not through a tunnel). WebAuthn requires a secure context (HTTPS or localhost).
- **Intent Compiler rejects a cart that should comply**: check the tag comparison — it's case-sensitive and exact-match. If `gelateria.yaml` has `"vegan"` and the policy says `"Vegan"`, that's a mismatch. Debug by printing both tag sets in `verify_cart_against_policy`.
```

---
```

---

# Day 6 — Merchant Reasoning Agent (A2A)

**Date: Monday, August 31, 2026.**

Today you build the process the plan is most paranoid about — the merchant _reasoning_ agent. It's the piece with the LLM in it on the merchant's side, and per the architecture diagram from Day 1, it holds **no signing key and no Razorpay write credentials, full stop.** Today is where you prove that isn't just a comment in a README.

**No changes for the Intent Compiler.** The A2A merchant reasoning agent is entirely orthogonal to the checkout path — it provides cross-sell suggestions, campaign drafts, and finance Q&A, none of which touch money or policies. The Intent Compiler runs inside the merchant execution server's `checkout_confirm`, which the reasoning agent never calls. Build this day exactly as the original plan describes.

---

## Changes Made

None. The A2A merchant reasoning agent is unchanged. The intent compiler runs in the merchant execution server, not in the reasoning agent. The reasoning agent's three skills (cross_sell, campaign_draft, finance_qa) are read-only and do not interact with the Intent Compiler.

## Patch/Append Content

No patches needed. Proceed with the original Day 6 content as-is.

---
```

---

# Day 7 — Failure Paths (The Differentiator)

**Date: Tuesday, September 1, 2026.**

The plan calls this day "the differentiator" for a reason worth sitting with before you write a line of code: almost anyone can demo a happy path. What's rare — and what a technical judge or reviewer actually notices — is a system that was deliberately attacked by its own builder, and visibly survived. Today you write eight adversarial tests against your own system, and confirm each one fails _safely, loudly, and traceably_ — not silently, not with a crash, not with an ambiguous half-state.

## What you'll have by tonight

A `tests/` suite covering **nine** failure modes end-to-end (eight from the original plan + one new Intent Compiler policy-violation test), an `isolation` test enforcing Day 5's "buyer agent imports nothing from merchant" rule at the AST level, and — critically — a rehearsed, two-minute Day 9 demo segment where you show 2–3 of these live, on purpose, in front of an audience.

---

## Changes Made

1. **New failure mode §9**: "Intent Compiler policy violation" — a cart that violates a signed policy is hard-rejected
2. **Failure mode §7 (prompt injection) updated**: now also demonstrates that the Intent Compiler catches injected items structurally
3. **"What you'll have by tonight" updated**: "eight" → "nine" failure modes
4. **Exit check updated**: includes the new policy-violation test

## Patch/Append Content

### Update "What you'll have by tonight"

Replace "eight distinct failure modes" with "nine distinct failure modes".

### Append new failure mode §9 — after existing §8 (token revocation), before the isolation test

```markdown
### 9. Intent Compiler policy violation

**Testing**: register a policy with `allowed_tags: ["vegan", "dairy-free"]`, then attempt `checkout_confirm` with a cart containing an item whose tags are `["coffee", "gift"]` (e.g., `gel-005` Tiramisu). **Correct**: the Intent Compiler hard-rejects with a specific tag-violation reason; no order is created; the rejection appears in `#merchant-server` with `level="blocked"` and in `/admin/audit` with `success=False`.

```python
def test_intent_compiler_rejects_policy_violation(cart_with_non_vegan_items, valid_confirm_token, signed_intent_policy):
    """A cart containing items not covered by the signed policy must be
    rejected by the Intent Compiler — no order created, no Razorpay call."""
    # signed_intent_policy fixture: allowed_tags=["vegan", "dairy-free"]
    # cart_with_non_vegan_items fixture: contains gel-005 (tags: coffee, gift)

    checkout_id = initiate_checkout(cart_with_non_vegan_items, valid_confirm_token)

    resp = call_mcp_tool("checkout_confirm", {
        "intent_checkout_id": checkout_id,
        "idempotency_key": str(uuid.uuid4()),
    }, valid_confirm_token)

    assert resp.status_code == 403
    assert "intent compiler" in resp.json()["detail"].lower()
    assert "tag" in resp.json()["detail"].lower() or "not allowed" in resp.json()["detail"].lower()

    # Verify no order was created
    assert_no_order_created(checkout_id)

    # Verify blocked trace in merchant-server channel
    assert_trace_emitted(
        channel="merchant-server",
        title_contains="Intent Compiler REJECTED",
        level="blocked",
    )

    # Verify audit row
    assert_audit_row_exists(tool="checkout_confirm", success=False)
```

This is the single most important new test for the Intent Compiler demo. When you rehearse for Day 9, this test is the one you show as Beat 7's third failure mode: "watch me try to buy a non-vegan item when the human's policy says vegan-only — the Intent Compiler catches it structurally, not because the LLM was told not to."

The test asserts on the _database record_ (no order created), not on the chat transcript — same discipline as the existing prompt-injection test. The Intent Compiler's rejection is a server-side mathematical fact, not an LLM judgment call.
```

### Update failure mode §7 (prompt injection) — add Intent Compiler angle

Append this paragraph to the end of the existing prompt-injection test description:

```markdown
The Intent Compiler adds a _second_ layer of defence here that the original plan didn't
have: even if the LLM is completely fooled by the injection and adds the malicious item
to the cart, the Intent Compiler will reject it at checkout time if the item's tags
don't match the signed policy. This is defence-in-depth: the LLM might be fooled (Layer 1
fails), but the server-side policy check catches it anyway (Layer 2 holds). Demo both
layers failing independently on Day 9: "the LLM was fooled — watch — but the purchase
still can't complete, because the Intent Compiler catches the tag violation."
```

### Update the isolation test — no changes needed

The isolation test (`tests/test_isolation.py`) is unchanged — it checks that `buyer_agent/` imports nothing from `merchant/` or `merchant_agent/`, which remains true.

### Update "Exit check" section

Replace with:

```markdown
## Exit check (from the plan)

> ✅ Exit: all 9 failure modes handled correctly and demonstrably (trace + audit log evidence for each), isolation test passes.

Run the whole suite: `uv run pytest tests/ -v`. All nine failure-mode tests green,
the isolation test green. For the Intent Compiler policy-violation test specifically:
manually pull up `#merchant-server` and look at the blocked embed — it should show
"Intent Compiler REJECTED checkout" with the specific reason (e.g., "Item 'gel-005'
has tags ['coffee', 'gift'] but policy only allows ['vegan', 'dairy-free']"). That
embed, visible to anyone watching the Discord channel during a live demo, is the
most visceral proof the policy enforcement is real.
```

### Update "What could go wrong" — append Intent Compiler entry

Append:

```markdown
- **Intent Compiler test passes for the wrong reason**: if the test fixture's `allowed_tags` doesn't exactly match what `gelateria.yaml` defines (e.g., a trailing space in `"vegan "` vs `"vegan"`), the test might pass because of an _unrelated_ tag mismatch rather than the specific non-vegan item you intended to test. Print the exact tag sets in the test output and confirm the intersection is what you expect before asserting on the rejection reason.
```

---
```

---

# Day 8 — Genericity, Polish, Freeze

**Date: Wednesday, September 2, 2026.**

**Feature freeze happens at the end of today.** After today, you write no new features — only fixes to things that are broken, and rehearsal. This isn't a soft guideline; it's the mechanism that makes September 4–5 into real slack instead of two more days of scope creep eating your buffer. Read that sentence again before you start today's work, because the temptation to "just add one more thing" is highest exactly when you're this close to done.

## What you'll have by tonight

Genericity proven with a second merchant config (if not already done on Day 7), the Intent Policy signing ceremony page polished, the four Discord channels visually coherent as a real observability dashboard, the `/admin/audit` page and storefront given a basic pass of polish, the `cloudflared` tunnel URL problem (Risk R4) solved with a startup script, and a fully rehearsed run-through of the Day 9 demo script — timed.

---

## Changes Made

1. **"What you'll have by tonight" updated**: added "Intent Policy signing ceremony page polished"
2. **New Step 1.5**: Intent Policy signing page polish (consistent styling, error states)
3. **Genericity proof updated**: the second merchant config must also work with the Intent Compiler (different `merchant_id` in the policy, different allowed tags)
4. **Rehearsal updated**: includes the WebAuthn signing ceremony as a demo beat

## Patch/Append Content

### Update "What you'll have by tonight"

Add "the Intent Policy signing ceremony page polished" to the list of tonight's deliverables.

### Append new Step 1.5 — Signing ceremony polish (after Step 1, before Step 2)

```markdown
### Step 1.5 — Intent Policy signing ceremony polish

Give `merchant/storefront/intent_sign.html` one pass of polish consistent with the
storefront page from Day 1:

- **Loading state**: disable the "Sign with Passkey" button and show a spinner while
  the WebAuthn ceremony is in progress. The ceremony can take 1–3 seconds on some
  devices; a button that appears frozen confuses users.
- **Success state**: after signing, replace the form with a clear confirmation:
  "Intent Policy registered. Your agent may now shop within these bounds."
  Include a summary of the policy (max spend, allowed tags) so the human can
  visually confirm what they just signed.
- **Error handling**: show specific error messages for common failures:
  - "Passkey cancelled" (user dismissed the biometric prompt)
  - "Passkey not available" (browser doesn't support WebAuthn, or not on HTTPS/localhost)
  - "Server error" (generic fallback)

Budget: 30 minutes. The signing page is shown once per session — it's not worth
spending more time on it than the audit page or storefront.
```

### Update the genericity proof section — add Intent Compiler angle

Append this paragraph to the end of Step 1 (genericity):

```markdown
**Intent Compiler genericity**: when booting the second merchant with `MERCHANT_CONFIG_PATH`
pointed at `chai.yaml`, the signing ceremony must also work — the human signs a new policy
with `merchant_id: "chai-wala"` (or whatever the second merchant's ID is) and different
allowed tags. Confirm the Intent Compiler correctly rejects a cart from the gelateria
merchant against the chai-wala policy (wrong merchant check), and accepts a cart from
the chai-wala merchant that complies. This is a second axis of genericity proof: the
same Intent Compiler code works across different merchants, different policies, different
tag sets — zero code changes, only different config and a different signed policy.
```

### Update the rehearsal section — add WebAuthn ceremony beat

Replace the existing rehearsal paragraph with:

```markdown
### Step 5 — Full rehearsal, timed

Run the entire Day 9 demo script (you'll write the script itself as part of tomorrow's
file, but rehearse the _system_, end to end, today) using your `dev_up.sh` from Step 2:
fresh start, storefront, **Intent Policy signing ceremony** (open the signing page, sign
with passkey, confirm policy is registered), discovery, OAuth consent, a full
Discord-driven purchase, the cross-sell moment from Day 6, and 2–3 of Day 7's failure-mode
demos (including the Intent Compiler policy-violation test). Time the whole thing. The
WebAuthn ceremony should take under 15 seconds — if it takes longer, the user experience
needs work, not more time.

If it runs long, this is the day to trim _narration_, not to cut a technical component —
the technical content is what's being evaluated; a slightly rushed but complete walkthrough
beats a smooth but incomplete one.
```

### Update "What could go wrong" — replace with Intent Compiler-aware version

Replace the entire "What could go wrong" section with:

```markdown
## What could go wrong

- **You discover during rehearsal that a Day 4–7 component is flakier than you thought**: this is precisely what rehearsal is _for_ — better to find it today than live on Day 9. Fix the specific flakiness (don't rewrite the component; find the actual bug), re-rehearse just that segment, move on.
- **The tunnel-registration script works once and then silently stops updating the webhook on subsequent runs**: check whether Razorpay's API call you're using is actually an _update_ (replacing the existing webhook config) versus accidentally _creating a new, additional_ webhook registration every restart, leaving multiple stale ones pointed at dead URLs alongside the current one.
- **You notice a real, previously-undiscovered security gap while polishing**: this is the one legitimate exception to "no new features after today" — a bug in the core money/policy/auth path is always in scope to fix, at any point up to the literal start of the Day 9 demo.
- **WebAuthn ceremony doesn't work in the demo browser**: WebAuthn requires HTTPS or localhost. If the demo machine's browser doesn't support passkeys (older browser, no biometric hardware), fall back to the OTP path — uncomment the OTP issuance in `checkout_initiate` and show the original per-transaction approval flow. This is exactly what the fallback path exists for.
```

---
```

---

# Day 9 — Demo Day

**Date: Thursday, September 3, 2026.**

No new code today, per yesterday's freeze — unless you find a genuine, demo-blocking bug, in which case fix exactly that and nothing else. Today is about presentation: a rehearsed script, a fallback recording in hand, and a clear, honest telling of what you built and why each piece is there.

---

## Changes Made

1. **Beat 4 updated**: the Discord-driven purchase now flows through the Intent Compiler (no approval pause)
2. **New Beat 4.5**: "The Intent Policy signing ceremony" — a dedicated demo beat showing the human signing a policy via passkey
3. **Beat 5 updated**: fingerprint correlation now includes the Intent Compiler verification trace
4. **Beat 7 updated**: includes the Intent Compiler policy-violation failure mode as a third demo beat
5. **Beat 9 updated**: honest caveat now mentions the Intent Compiler's WebAuthn basis, not just OTP
6. **Demo script total beats**: 10 (original) → 11 (with signing ceremony beat)

## Patch/Append Content

### Update Beat 4 — remove approval-pause language

Replace the existing Beat 4 text with:

```markdown
### Beat 4 — The Discord-driven purchase

From `#buyer-agent`, type a real request. Let the audience watch: search → cross-sell
suggestion arriving via a real A2A call to a separate process (point this out explicitly —
"that suggestion just came from a second, isolated process over a standard agent-to-agent
protocol, not a hardcoded string") → checkout-initiate → checkout-confirm. There is no
approval pause, no DM, no OTP — the Intent Compiler verifies the cart against the signed
policy in real-time, and the order is created immediately. "The human signed the policy
once. The agent shopped within those bounds. The server verified mathematically. No human
approval was needed at checkout time."
```

### Insert new Beat 4.5 — the signing ceremony (between Beat 4 and Beat 5)

```markdown
### Beat 4.5 — The Intent Policy signing ceremony

Show the signing ceremony page you built on Day 4. Walk through it live: set the
constraints (₹500 max, vegan + dairy-free tags, gelateria-roma merchant), click
"Sign with Passkey," complete the biometric. Show the success confirmation with the
policy summary.

"This is the only time the human needs to be online for the entire shopping session.
They're signing a machine-readable policy — max spend, allowed product tags, merchant
lock — using the same passkey they use to log into their laptop. The signature proves
this specific human authorized these specific constraints. Once signed, the agent can
shop autonomously within these bounds. If it tries to exceed them, the server rejects
the checkout — not because the LLM was told not to, but because the math doesn't
check out."
```

### Update Beat 5 — include Intent Compiler trace

Replace the existing Beat 5 text with:

```markdown
### Beat 5 — The Intent Compiler verification (one of the plan's "moments that win the rubric")

After the checkout-confirm call succeeds, show three things simultaneously:

1. **The Intent Compiler trace in `#merchant-server`**: a green embed showing
   "Intent Compiler: cart complies with policy" with the total and item count.
2. **The signing ceremony confirmation**: the policy summary showing "max ₹500,
   vegan + dairy-free, gelateria-roma" — the exact constraints that were just
   verified.
3. **The `/admin/audit` row**: `checkout_confirm`, `success=True`, the same
   `trace_id` as the Intent Compiler embed.

"These three things prove the same mathematical fact from three angles: the human
signed a policy (passkey), the agent shopped within it (cart has only vegan items
under ₹500), and the server verified it (Intent Compiler trace). None of them
required the human to be online at checkout time. None of them involved a 6-digit
code. The security is in the math, not in the human's attentiveness."
```

### Update Beat 7 — add Intent Compiler as third failure mode

Replace the existing Beat 7 text with:

```markdown
### Beat 7 — Break it, on purpose (the second "moment that wins the rubric")

This is where Day 7 pays off. Pick 3 of your rehearsed failure modes:

1. **Idempotent retry** — "I'm about to simulate the exact ambiguous situation a real
   network timeout creates — watch what happens when the same request comes back."
2. **Tampered mandate** (if showing the fallback OTP path) or **Cart hash mismatch** —
   "watch me try to alter the cart after the policy was signed."
3. **Intent Compiler policy violation** — "watch me add a non-vegan item to the cart
   when the human's policy says vegan-only." Add `gel-005` (Tiramisu, tags: coffee,
   gift) to the cart, attempt checkout, and show the Intent Compiler rejecting with
   "Item 'gel-005' has tags ['coffee', 'gift'] but policy only allows ['vegan',
   'dairy-free']." "The LLM might have been fooled by a prompt injection into adding
   this item — but the Intent Compiler doesn't care what the LLM thinks. It checks
   the tags mathematically. The defence is architectural, not instructional."

For each: state what you're about to attempt and why it should fail, do it live, and
point at the resulting blocked trace/audit row as it appears.
```

### Update Beat 9 — honest caveat with Intent Compiler framing

Replace the existing Beat 9 text with:

```markdown
### Beat 9 — Close with the honest caveat

Restate the precision point: the Intent Compiler uses WebAuthn — the same authenticator
billions of devices already support — to bind a human's biometric signature to a
machine-readable policy. This is stronger than OTP in one specific, important way: the
policy is a mathematical constraint that the server enforces, not a human who might click
"Approve" on a poisoned embed without reading carefully. But it has its own honest limits:

- The human signs the policy once. If the policy itself is too broad (e.g., `max_spend:
  100000` when the human meant ₹500), the Intent Compiler cannot catch that — it
  enforces what was signed, not what was intended.
- WebAuthn is phishing-resistant for the signing ceremony (the biometric is local to the
  device), but the _policy document itself_ is rendered in a browser page the human must
  trust. A compromised merchant server could theoretically serve a misleading signing
  page. The mitigation is that the policy is short, human-readable, and displayed both
  at signing time and in the audit trail.
- The architecture proves: the reasoning agent cannot hold the signing key (same as the
  original OTP claim, but now the key is in the human's device, not the server's
  filesystem). What it does _not_ prove: that the human who signed the policy is the
  same person who owns the payment instrument. That's an identity-binding problem this
  project does not claim to solve.

End on what would need to change for production: signed policies in a hardware security
module, per-transaction freshness (a policy that expires per-purchase rather than per-day),
and integration with AP2/Verifiable-Intent standard policy formats. State these briefly —
this shows you understand the boundary of what you built, which is itself a mark of the
engineering judgment a technical audience is evaluating.
```

### Update the "If something breaks live" section — add WebAuthn-specific fallback

Append to the existing section:

```markdown
If the WebAuthn ceremony specifically fails (passkey not available on the demo machine,
biometric hardware malfunction), this is the exact scenario the OTP fallback path exists
for. Quickly uncomment the OTP issuance in `checkout_initiate` (a one-line change you
prepared on Day 8), and run the demo through the original per-transaction approval flow.
Narrate the transition honestly: "the passkey ceremony didn't work on this machine —
here's the fallback path, which uses Discord DM + OTP instead. The security property
is weaker (human attentiveness vs. mathematical constraint), but the architecture
accommodates both." A graceful fallback under pressure is more impressive than a
perfect happy path that was never tested under stress.
```

### Update the "After the demo" section — add Intent Compiler future work

Append to the existing section:

```markdown
If you want to continue developing the Intent Compiler past today: per-transaction
freshness (each checkout requires a fresh WebAuthn assertion, not a stored policy),
policy delegation (allowing the human to delegate sub-policies to specific agents),
and alignment with AP2 Mandate / Mastercard Verifiable Intent standard policy formats
are the natural next steps — each is a meaningful project on its own.
```

---
```

---

# Summary of all changes

| Day | New concepts | Modified steps | New steps | Tests changed |
|-----|-------------|----------------|-----------|---------------|
| 4 | §8 WebAuthn, §9 Intent Compiler | §1 state machine, §2 OTP fallback, Step 3 initiate, Step 6 confirm | Step 2.5 models, Step 4.5 signing page, Step 4.6 WebAuthn routes, Step 4.7 verify function | — |
| 5 | — (updated §3 interrupt note) | Step 3 graph.py (no approval pause), Step 4 bot.py | Step 3.5 trigger signing | — |
| 6 | — | — | — | — |
| 7 | — | §7 prompt injection updated | — | §9 policy-violation test |
| 8 | — | genericity proof, rehearsal | Step 1.5 signing page polish | — |
| 9 | — | Beat 4, 5, 7, 9 | Beat 4.5 signing ceremony | — |

**Dependency changes:**
- `pyproject.toml`: add `fido2` to `dependencies`
- `uv.lock`: regenerated after `uv add fido2`

**New files created:**
- `merchant/intent_compiler.py` — the pure `verify_cart_against_policy()` function
- `merchant/intent_routes.py` — WebAuthn challenge/registration/verification routes + signing page route
- `merchant/storefront/intent_sign.html` — the signing ceremony HTML page

**Modified files:**
- `merchant/models.py` — add `IntentPolicy`, `SignedIntentPolicy`, `IntentPolicyRow`, `PolicyChallenge`
- `merchant/mcp_server.py` — update `checkout_initiate` and `checkout_confirm` signatures
- `merchant/app.py` — mount `intent_router`

**Preserved unchanged:**
- `merchant/razorpay_client.py` — identical
- `merchant/webhooks.py` — identical
- `merchant/mandate.py` — identical (used as fallback path)
- `merchant/trace.py` — identical
- `merchant/policy.py` — identical
- `merchant_agent/` — entire directory unchanged
- `buyer_agent/` — graph simplified (no interrupt), but isolation preserved
- All existing tests — unchanged (new tests added, not replaced)
