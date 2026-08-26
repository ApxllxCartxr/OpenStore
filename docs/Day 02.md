# Day 2 — OAuth 2.1 Authorization Server

**Date: Thursday, August 27, 2026.**

Today's concept load is the heaviest of the whole project. OAuth is infamous for being simultaneously ubiquitous ("Sign in with Google") and genuinely confusing the first time you build one instead of just consuming one. Take the concepts section seriously — everything in Days 3, 5, and 6 assumes you understand _why_ a bearer token has a scope, not just that it does.

## What you'll have by tonight

A real, working OAuth 2.1 Authorization Server, mounted inside your FastAPI app: client registration, an authorization endpoint with a human consent screen, PKCE verification, token issuance and refresh, revocation, and a `require_scope` dependency that every money-touching endpoint from Day 3 onward will use to gate access.

## Risk called out in the plan

> R5: OAuth AS eats 2 days instead of 1. Hard stop at end of Day 2. Fallback: pre-registered clients (skip DCR), keep PKCE + scopes.

If you're not done by tonight, cut Dynamic Client Registration (the `/oauth/register` endpoint) and hand-insert a client row into the database instead. Keep everything else. This is called out explicitly in the plan's cut-lines (§8, item 3) — it is not a failure to take this cut, it's the schedule working as designed.

---

## Concepts

### 1. The problem OAuth actually solves

Here's the situation without OAuth: an AI buyer agent wants to act on your behalf at a merchant's server — search products, build a cart, eventually spend your money. The naive approach is: you give the agent your merchant account password, and it logs in as you. This is bad for reasons that generalize past this project:

- The agent now has **everything** you can do, forever, until you change your password — there's no way to say "you can search products and manage a cart, but you can never directly withdraw funds without me approving each specific charge."
- If the agent is compromised, buggy, or just wrong, the blast radius is your entire account.
- You can't tell your merchant server "revoke _just this agent's_ access" without changing your password and breaking every other agent/integration that also had it.

**OAuth 2.1** (the tightened, security-best-practices consolidation of OAuth 2.0's many extensions — the version this project targets) is a protocol for a **resource owner** (you, the human) to grant a **client** (the buyer agent) **limited, revocable, time-bound access** to a **resource server** (your merchant server) — without the client ever seeing your password, and with the access precisely scoped to only what you approved.

The core trick is indirection: instead of the client authenticating _as you_, an **authorization server** (a separate role your merchant server also plays here — production systems often split this into a separate service, but nothing stops it living in the same process) issues the client a **token** — a piece of data proving "this specific client, for this specific scope of actions, until this specific time, because a human explicitly approved it." Your resource server endpoints don't check passwords; they check tokens.

### 2. Scopes: turning "can this client do X" into data, not code

A **scope** is a string naming one unit of permission — in OpenStore: `catalog:read`, `cart:write`, `checkout:initiate`, `checkout:confirm`. When a client requests access, it requests specific scopes. When the human approves, they approve specific scopes (visibly, on the consent screen — "OpenStore Buyer wants to: read your catalog, manage a cart, initiate a checkout"). The resulting token carries exactly those scopes, and nothing else.

This is why the plan's MCP tool table (from Day 1's contract) ties every tool to exactly one scope, and every route checks it: `search_products` needs only `catalog:read`; `checkout_confirm` needs `checkout:confirm`. A token scoped to `catalog:read` alone genuinely _cannot_ call `checkout_confirm` — not because of an instruction the agent is trusted to follow, but because the server checks the token's scope list and refuses the call if the needed scope isn't present. This is the same "architecture, not instruction" principle from the top-level thesis, applied one layer down: scopes are how you make "this agent can browse but not spend" a structural fact instead of a polite request.

### 3. The Authorization Code flow, step by step

There are several OAuth "grant types" (ways of getting a token); OAuth 2.1 narrows the recommended set considerably compared to 2.0, and for a client like our buyer agent (which runs on a user's behalf and can involve a human in the loop), the **Authorization Code flow** is the one you want. Walk through it once, slowly, because every endpoint you write today is one step of this:

1. **Client redirects the user to the authorization server's `/authorize` endpoint**, with its `client_id`, the `scopes` it wants, a `redirect_uri` to send the user back to afterward, and (per PKCE, next section) a `code_challenge`.
2. **The authorization server shows the human a consent screen**: "OpenStore Buyer wants to: [scopes, in plain language]. Approve?" The human is authenticating to the _authorization server_ here (in a full system, via login; in OpenStore, we're simplifying since there's one merchant admin) — never to the client.
3. **The human clicks Approve.** The authorization server generates a short-lived, single-use **authorization code**, and redirects the browser back to the client's `redirect_uri` with that code attached as a query parameter.
4. **The client takes that code and calls `/token`** on the authorization server directly (server-to-server, not through the browser), along with its PKCE `code_verifier`, exchanging the code for an actual **access token** (and often a **refresh token**).
5. **The client uses the access token** on every subsequent API call, typically as an HTTP header: `Authorization: Bearer <token>`.

Why not just hand back the access token directly at step 3, skipping the code-then-exchange dance? Because step 3's redirect happens through the user's browser — URLs get logged in browser history, proxy logs, referrer headers. A short-lived, single-use _code_ leaking is much lower-stakes than the actual access token leaking, since the code by itself is useless without also presenting the PKCE verifier in step 4 (next section) — and it can only be redeemed once.

### 4. PKCE — why a "public client" needs an extra proof

**PKCE** (Proof Key for Code Exchange, pronounced "pixy") solves a specific problem: OAuth was originally designed assuming the client is a confidential server that can keep a `client_secret` truly secret. But our buyer agent — like a mobile app, or any native/desktop application — is a **public client**: its code runs on a machine the user (or an attacker with access to that machine) can inspect. A `client_secret` embedded in a public client's code isn't actually secret. Without a mitigation, an attacker who intercepts an authorization code midway through step 3 above (e.g., another app registered to catch the same redirect scheme) could exchange it for a token themselves.

PKCE fixes this without needing a durable secret at all, using one-time-per-request math:

1. Before starting, the client generates a random string, the **`code_verifier`** (kept only in memory, never sent to the authorization server until step 4).
2. It computes `code_challenge = BASE64URL(SHA256(code_verifier))`, and sends _that_ (not the verifier) in step 1's authorize request.
3. At step 4 (token exchange), the client finally sends the raw `code_verifier`.
4. The authorization server independently hashes the received `code_verifier` and checks it matches the `code_challenge` it received back in step 1.

An attacker who intercepts the authorization code from step 3's redirect still can't redeem it, because they never saw the `code_verifier` — only its hash passed through the authorize request, and hashes aren't reversible. This is why OAuth 2.1 (unlike 2.0) makes PKCE **mandatory for all clients**, not just public ones — it's cheap and closes a real class of attack even for confidential clients.

### 5. The loopback redirect pattern

Where does a `redirect_uri` even point, for a Discord bot or a CLI tool that has no web server of its own listening on the public internet? The answer, standardized for exactly this case, is **loopback redirect**: the client, right before starting the OAuth flow, spins up a tiny local HTTP server on `127.0.0.1` (a fixed or dynamically-chosen port — this project uses a fixed `:8765`), and registers `http://127.0.0.1:8765/callback` as its redirect URI. The authorization server's step-3 redirect goes to that address — which only resolves _on the same machine the client is running on_ — and the client's tiny local server catches the incoming code, then immediately shuts itself down. This is legal, standard practice for native/public clients under OAuth 2.1 (explicitly permitted, unlike some other localhost patterns that get flagged as insecure).

### 6. What a JWT actually is

You'll use JWTs (JSON Web Tokens) for the OAuth access tokens today, and again for the mandate on Day 4 — worth understanding properly once, since it recurs.

A JWT is three base64url-encoded segments joined by dots: `header.payload.signature`.

- **Header**: a small JSON object naming the signing algorithm (e.g., `{"alg": "RS256", "typ": "JWT"}` or, for us later, `{"alg": "EdDSA"}`) and often a **`kid`** (key ID), identifying _which_ key was used, so a verifier with multiple keys on file knows which one to check against.
- **Payload** (also called **claims**): the actual data — for an OAuth access token, things like `sub` (subject — whose token this is, here the OAuth `client_id`), `scope`, `exp` (expiry, as a Unix timestamp), `iat` (issued-at).
- **Signature**: a cryptographic signature over the header and payload, computed with the issuer's private key. Anyone holding the corresponding public key can _verify_ the signature (proving the token wasn't tampered with and really was issued by the authorization server) without being able to _forge_ a new one (since they don't have the private key).

Critically: **the payload of a JWT is only base64-encoded, not encrypted.** Anyone can decode it and read the claims — try it on [jwt.io](https://jwt.io/) with any JWT. The signature guarantees _integrity_ (nobody tampered with it) and _authenticity_ (it really came from whoever holds the private key), **not confidentiality**. Never put a secret value inside a JWT payload. This is a very common beginner mistake and worth internalizing now, since Day 4's mandate JWT will carry an actual cart and amount — sensitive in the sense of "must not be tampered with," but not secret in the sense of "must be hidden," since the whole design _wants_ those fields to be inspectable by anyone who has the token.

You'll use `pyjwt` (already installed on Day 1) to encode and decode these. FastAPI access tokens in this project are signed **symmetrically** with `alg=HS256` (a single shared secret used for both signing and verifying) for simplicity — a reasonable simplification for the demo, since the same process both issues and verifies these tokens. (Day 4's mandate uses **asymmetric** Ed25519 signing instead, and that distinction matters there specifically because the merchant _reasoning_ agent needs to be structurally unable to forge a mandate even though it can run in a different process — more on that Day 4.)

### 7. Discovery metadata: `/.well-known/oauth-authorization-server`

OAuth defines a standard discovery document (RFC 8414) at a well-known URL, listing the authorization server's actual endpoint URLs, supported grant types, and supported scopes — so a client doesn't need those hardcoded, it can fetch this document and configure itself. This is the exact document your Day 1 `agent-commerce.json`'s `auth.authorization_server` field points to, and it's what the buyer agent will fetch on Day 5 before it does anything else.

---

## Build

### Step 1 — Discovery metadata endpoints

**What**: two static JSON documents describing your authorization server's capabilities. **Tool**: FastAPI routes returning Pydantic models (same pattern as Day 1's `agent-commerce.json`). **Why**: standard, expected shape (RFC 8414 for the AS metadata; RFC 9728 for protected-resource metadata) that any spec-compliant OAuth client library can parse without custom code.

`merchant/oauth/models.py`:

```python
from pydantic import BaseModel

class AuthServerMetadata(BaseModel):
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str
    revocation_endpoint: str
    jwks_uri: str
    scopes_supported: list[str]
    response_types_supported: list[str] = ["code"]
    grant_types_supported: list[str] = ["authorization_code", "refresh_token"]
    code_challenge_methods_supported: list[str] = ["S256"]
    token_endpoint_auth_methods_supported: list[str] = ["none"]  # public clients
```

`merchant/oauth/routes.py` (you'll keep adding to this file all day):

```python
from fastapi import APIRouter
from merchant.oauth.models import AuthServerMetadata

router = APIRouter()

BASE_URL = "http://localhost:8000"

@router.get("/.well-known/oauth-authorization-server")
def as_metadata():
    return AuthServerMetadata(
        issuer=BASE_URL,
        authorization_endpoint=f"{BASE_URL}/oauth/authorize",
        token_endpoint=f"{BASE_URL}/oauth/token",
        registration_endpoint=f"{BASE_URL}/oauth/register",
        revocation_endpoint=f"{BASE_URL}/oauth/revoke",
        jwks_uri=f"{BASE_URL}/oauth/jwks.json",
        scopes_supported=["catalog:read", "cart:write", "checkout:initiate", "checkout:confirm"],
    )

@router.get("/.well-known/oauth-protected-resource")
def protected_resource_metadata():
    return {
        "resource": BASE_URL,
        "authorization_servers": [BASE_URL],
        "scopes_supported": ["catalog:read", "cart:write", "checkout:initiate", "checkout:confirm"],
    }
```

Mount this router in `merchant/app.py`:

```python
from merchant.oauth.routes import router as oauth_router
app.include_router(oauth_router)
```

`APIRouter` is FastAPI's way of splitting routes across files instead of cramming everything into one `app.py` — `include_router` merges its routes into the main app. You'll use this same pattern for the MCP mount on Day 3.

### Step 2 — Dynamic Client Registration: `POST /oauth/register`

**What**: an endpoint a client calls once, up front, to register itself and receive a `client_id` (and, if it were a confidential client, a `client_secret` — ours is public, so this is mostly bookkeeping). **Tool**: SQLModel insert against the `OAuthClient` table from Day 1. **Why**: lets a new agent onboard itself without a human manually inserting a database row — this is the piece the plan says to cut first if Day 2 runs long (§8, cut-line 3), since a pre-registered client (inserted by hand, once) achieves the same demo outcome with less code.

```python
import secrets
from fastapi import APIRouter, Depends
from sqlmodel import Session
from merchant.db import get_session
from merchant.models import OAuthClient
from pydantic import BaseModel

class ClientRegistrationRequest(BaseModel):
    client_name: str
    redirect_uris: list[str]

class ClientRegistrationResponse(BaseModel):
    client_id: str
    client_name: str
    redirect_uris: list[str]

@router.post("/oauth/register", response_model=ClientRegistrationResponse)
def register_client(req: ClientRegistrationRequest, session: Session = Depends(get_session)):
    client_id = f"client_{secrets.token_urlsafe(12)}"
    client = OAuthClient(
        client_id=client_id,
        client_secret_hash="",  # public client, no secret
        display_name=req.client_name,
        redirect_uris=req.redirect_uris,
    )
    session.add(client)
    session.commit()
    return ClientRegistrationResponse(
        client_id=client_id,
        client_name=req.client_name,
        redirect_uris=req.redirect_uris,
    )
```

`secrets.token_urlsafe(12)` generates a cryptographically secure random string safe to embed in a URL — from Python's standard `secrets` module (never use `random` for anything security-relevant; `random` is not cryptographically secure and is predictable given enough samples). `session.add(client)` stages the insert; `session.commit()` actually writes it and assigns the auto-incrementing `id`.

**Cut-line reminder**: if you're behind schedule, skip this endpoint and instead insert a row directly:

```python
# One-time manual registration, if DCR is cut:
# python -c "from merchant.db import engine; from sqlmodel import Session; from merchant.models import OAuthClient; \
#   s = Session(engine); s.add(OAuthClient(client_id='buyer-agent-dev', client_secret_hash='', display_name='OpenStore Buyer', redirect_uris=['http://127.0.0.1:8765/callback'])); s.commit()"
```

### Step 3 — `GET /oauth/authorize` and the consent page

**What**: the endpoint a client redirects the human's browser to, which renders a consent screen and, on approval, issues an authorization code. **Tool**: Jinja2 template (same mechanism as Day 1's storefront), an in-memory or database-backed store for pending authorization codes. **Why**: this is the actual human-in-the-loop moment of OAuth — the point where a person, not code, decides to grant access. Everything before this point is setup; everything after is mechanical verification.

Add an in-memory dict for now (fine for a demo; swap for a database table if you want it to survive restarts):

```python
# merchant/oauth/store.py
import time

# code -> {client_id, scopes, code_challenge, redirect_uri, expires_at}
pending_codes: dict[str, dict] = {}
```

The authorize route:

```python
import secrets
import time
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from merchant.oauth.store import pending_codes

templates = Jinja2Templates(directory="merchant/oauth/templates")

@router.get("/oauth/authorize", response_class=HTMLResponse)
def authorize(
    request: Request,
    client_id: str,
    scope: str,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str = "S256",
    state: str = "",
):
    return templates.TemplateResponse(
        "consent.html",
        {
            "request": request,
            "client_id": client_id,
            "scopes": scope.split(),
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "state": state,
        },
    )

@router.post("/oauth/consent")
def consent(
    approve: str,  # "yes" or "no", from the form submit button
    client_id: str,
    scope: str,
    redirect_uri: str,
    code_challenge: str,
    state: str = "",
):
    if approve != "yes":
        return RedirectResponse(f"{redirect_uri}?error=access_denied&state={state}")

    code = secrets.token_urlsafe(24)
    pending_codes[code] = {
        "client_id": client_id,
        "scopes": scope.split(),
        "code_challenge": code_challenge,
        "redirect_uri": redirect_uri,
        "expires_at": time.time() + 60,  # 60 seconds to redeem
    }
    return RedirectResponse(f"{redirect_uri}?code={code}&state={state}")
```

`mkdir -p merchant/oauth/templates`, then `merchant/oauth/templates/consent.html`:

```html
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Authorize {{ client_id }}</title></head>
<body>
  <h1>{{ client_id }} wants to:</h1>
  <ul>
    {% for s in scopes %}
    <li>{{ s }}</li>
    {% endfor %}
  </ul>
  <form method="post" action="/oauth/consent">
    <input type="hidden" name="client_id" value="{{ client_id }}">
    <input type="hidden" name="scope" value="{{ scopes|join(' ') }}">
    <input type="hidden" name="redirect_uri" value="{{ redirect_uri }}">
    <input type="hidden" name="code_challenge" value="{{ code_challenge }}">
    <input type="hidden" name="state" value="{{ state }}">
    <button type="submit" name="approve" value="yes">Approve</button>
    <button type="submit" name="approve" value="no">Deny</button>
  </form>
</body>
</html>
```

This is intentionally the plainest possible consent form — the plan explicitly says storefront/UI visual polish is a cut-line, never the security-relevant logic. What matters is that a human sees the _specific requested scopes in plain language_ and makes an explicit choice — not how pretty the buttons are.

### Step 4 — `POST /oauth/token`: exchanging the code (or refreshing)

**What**: the endpoint that validates the authorization code and PKCE verifier, and issues a signed JWT access token plus a refresh token. **Tool**: `pyjwt` for signing, `hashlib`/`base64` for the PKCE check. **Why**: this is the step where "a human approved this" (step 3) becomes "here is a bearer credential the client can actually use" — the mechanical trust handoff.

```python
import base64
import hashlib
import time
import jwt as pyjwt
import secrets
from fastapi import Form
from merchant.oauth.store import pending_codes

JWT_SECRET = "dev-only-change-me"  # in real deployment: load from env, never hardcode
ACCESS_TOKEN_TTL_SECONDS = 15 * 60
REFRESH_TOKENS: dict[str, dict] = {}  # refresh_token -> {client_id, scopes}

def verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    computed = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return computed == code_challenge

@router.post("/oauth/token")
def token(
    grant_type: str = Form(...),
    code: str = Form(None),
    code_verifier: str = Form(None),
    redirect_uri: str = Form(None),
    refresh_token: str = Form(None),
    client_id: str = Form(...),
):
    if grant_type == "authorization_code":
        entry = pending_codes.pop(code, None)
        if entry is None:
            return {"error": "invalid_grant"}, 400
        if entry["expires_at"] < time.time():
            return {"error": "invalid_grant", "error_description": "code expired"}, 400
        if entry["client_id"] != client_id or entry["redirect_uri"] != redirect_uri:
            return {"error": "invalid_grant"}, 400
        if not verify_pkce(code_verifier, entry["code_challenge"]):
            return {"error": "invalid_grant", "error_description": "PKCE verification failed"}, 400

        scopes = entry["scopes"]

    elif grant_type == "refresh_token":
        entry = REFRESH_TOKENS.get(refresh_token)
        if entry is None or entry["client_id"] != client_id:
            return {"error": "invalid_grant"}, 400
        scopes = entry["scopes"]

    else:
        return {"error": "unsupported_grant_type"}, 400

    now = int(time.time())
    jti = secrets.token_urlsafe(16)
    access_token = pyjwt.encode(
        {
            "sub": client_id,
            "scope": " ".join(scopes),
            "iat": now,
            "exp": now + ACCESS_TOKEN_TTL_SECONDS,
            "jti": jti,
        },
        JWT_SECRET,
        algorithm="HS256",
    )

    new_refresh = secrets.token_urlsafe(24)
    REFRESH_TOKENS[new_refresh] = {"client_id": client_id, "scopes": scopes}

    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": ACCESS_TOKEN_TTL_SECONDS,
        "refresh_token": new_refresh,
        "scope": " ".join(scopes),
    }
```

Walk `verify_pkce` once, since it's the cryptographic heart of the endpoint: it re-derives the `code_challenge` from the `code_verifier` the client just revealed (SHA-256 hash, base64url-encoded, padding stripped per the PKCE spec — hence `.rstrip(b"=")`), and checks it matches what was recorded back when `/authorize` first saw the challenge. If an attacker only intercepted the code (from the browser redirect) but never had the verifier (which never left the legitimate client's memory), this check fails and the exchange is rejected.

### Step 5 — `require_scope`: the dependency every protected route uses

**What**: a reusable FastAPI dependency that extracts the bearer token from the `Authorization` header, verifies its signature and expiry, and checks a required scope is present — rejecting with a trace emission if not. **Tool**: FastAPI's `Depends`, `pyjwt.decode`. **Why**: this is the single choke point where "does this token actually permit this action" gets decided — every MCP tool from Day 3 onward calls through this, so getting it right once here means never re-deriving the check per-endpoint (and risking an inconsistent one).

```python
import uuid
from fastapi import Header, HTTPException
import jwt as pyjwt
from merchant.trace import emit

def require_scope(required_scope: str):
    def dependency(authorization: str = Header(...)) -> dict:
        trace_id = str(uuid.uuid4())
        if not authorization.startswith("Bearer "):
            emit("merchant-server", "Auth rejected", {"reason": "malformed header"}, trace_id, "blocked")
            raise HTTPException(401, "Malformed Authorization header")

        token = authorization.removeprefix("Bearer ")
        try:
            claims = pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        except pyjwt.ExpiredSignatureError:
            emit("merchant-server", "Auth rejected", {"reason": "token expired"}, trace_id, "blocked")
            raise HTTPException(401, "Token expired")
        except pyjwt.InvalidTokenError:
            emit("merchant-server", "Auth rejected", {"reason": "invalid signature"}, trace_id, "blocked")
            raise HTTPException(401, "Invalid token")

        scopes = claims.get("scope", "").split()
        if required_scope not in scopes:
            emit(
                "merchant-server", "Auth rejected",
                {"reason": "missing scope", "required": required_scope, "client": claims["sub"]},
                trace_id, "blocked",
            )
            raise HTTPException(403, f"Missing required scope: {required_scope}")

        return claims
    return dependency
```

This is a **dependency factory** — `require_scope("checkout:confirm")` is a function call that _returns_ the actual dependency function, closing over `required_scope`. You use it in a route like:

```python
@app.post("/agent/mcp/checkout_confirm")
def checkout_confirm_route(claims: dict = Depends(require_scope("checkout:confirm"))):
    client_id = claims["sub"]
    ...
```

Note `algorithms=["HS256"]` is passed explicitly as an **allowlist**, not inferred from the token itself. This matters more than it looks: a known JWT vulnerability class is an attacker crafting a token with `alg: none` (some libraries historically accepted this, treating it as "no signature needed") or switching the algorithm to trick a verifier into using the wrong key type. Always pass an explicit `algorithms=` allowlist to `jwt.decode` — never let the token's own header dictate how it gets verified. You'll apply this exact discipline again, more strictly, for the Ed25519 mandate on Day 4.

### Step 6 — Revocation: `POST /oauth/revoke`

**What**: an endpoint that invalidates a specific token (or its associated client) immediately, rather than waiting for natural expiry. **Tool**: a revocation list (in-memory set, or the `OAuthToken.revoked` column from Day 1's schema). **Why**: Day 7's failure-path demo ("Token revocation") needs this — revoke mid-session, confirm the next money call fails, but `catalog:read` calls using a _different_, non-revoked token still succeed.

```python
REVOKED_JTIS: set[str] = set()

@router.post("/oauth/revoke")
def revoke(token: str = Form(...)):
    try:
        claims = pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"], options={"verify_exp": False})
        REVOKED_JTIS.add(claims["jti"])
    except pyjwt.InvalidTokenError:
        pass  # revoking an already-invalid token is a no-op, not an error
    return {"revoked": True}
```

And add the revocation check into `require_scope`'s dependency, right after decoding:

```python
        if claims.get("jti") in REVOKED_JTIS:
            emit("merchant-server", "Auth rejected", {"reason": "token revoked"}, trace_id, "blocked")
            raise HTTPException(401, "Token has been revoked")
```

`options={"verify_exp": False}` on the revoke endpoint's decode call is deliberate: you want to be able to revoke a token even if it's already expired or about to be, since the goal is just to read its `jti` — you're not making an authorization decision here, just extracting an identifier.

---

## Running it

Restart your server (`uv run uvicorn merchant.app:app --reload --port 8000`) and drive the whole flow with `httpx` or `curl` by hand once, to _see_ it work before writing the automated test:

```bash
# 1. Register a client
curl -X POST http://localhost:8000/oauth/register \
  -H "Content-Type: application/json" \
  -d '{"client_name": "test-client", "redirect_uris": ["http://127.0.0.1:8765/callback"]}'
# note the returned client_id
```

Generate a PKCE pair in a Python shell:

```python
import secrets, hashlib, base64
verifier = secrets.token_urlsafe(32)
challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
print("verifier:", verifier)
print("challenge:", challenge)
```

Visit in a browser: `http://localhost:8000/oauth/authorize?client_id=<id>&scope=catalog:read+cart:write&redirect_uri=http://127.0.0.1:8765/callback&code_challenge=<challenge>&code_challenge_method=S256&state=xyz` — click Approve, note the `code=` query param you get redirected to (the redirect will fail to load since nothing's listening on :8765 yet, that's fine, just read it from the browser's address bar).

```bash
curl -X POST http://localhost:8000/oauth/token \
  -d "grant_type=authorization_code&code=<code>&code_verifier=<verifier>&redirect_uri=http://127.0.0.1:8765/callback&client_id=<id>"
```

You should get back a JSON blob with `access_token`, `refresh_token`, `expires_in`. Decode the `access_token` at [jwt.io](https://jwt.io/) (paste it in, no key needed to _read_ it — reinforcing the "JWTs aren't encrypted" point from Concepts) and confirm the `scope` claim matches what you requested.

## Exit check (from the plan)

> ✅ Exit: `pytest` drives the full PKCE dance headlessly and gets a scoped token.

Add `pytest`, `pytest-asyncio`, and `httpx`'s test client support (already have `httpx`; add `uv add --dev pytest pytest-asyncio`), and write `tests/test_oauth.py`:

```python
import base64
import hashlib
import secrets
from fastapi.testclient import TestClient
from merchant.app import app

client = TestClient(app)

def test_full_pkce_dance_issues_scoped_token():
    reg = client.post("/oauth/register", json={
        "client_name": "pytest-client",
        "redirect_uris": ["http://127.0.0.1:8765/callback"],
    })
    client_id = reg.json()["client_id"]

    verifier = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()

    consent = client.post("/oauth/consent", data={
        "approve": "yes",
        "client_id": client_id,
        "scope": "catalog:read cart:write",
        "redirect_uri": "http://127.0.0.1:8765/callback",
        "code_challenge": challenge,
        "state": "xyz",
    }, follow_redirects=False)
    location = consent.headers["location"]
    code = location.split("code=")[1].split("&")[0]

    token_resp = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": "http://127.0.0.1:8765/callback",
        "client_id": client_id,
    })
    body = token_resp.json()
    assert "access_token" in body
    assert "catalog:read" in body["scope"]
    assert "cart:write" in body["scope"]
```

`TestClient` (from `fastapi.testclient`, built on `httpx`) lets you call your own app's routes in-process, with no real network socket needed — the standard way to test a FastAPI app. Run it: `uv run pytest tests/test_oauth.py -v`. Green means you've actually verified the mechanics, not just eyeballed a `curl` response once.

## What could go wrong

- **PKCE verification always fails**: the single most common bug is a mismatch between how you strip base64 padding on encode vs. decode, or accidentally using standard base64 (`+`/`/` characters) instead of URL-safe base64 (`-`/`_`). `base64.urlsafe_b64encode` handles the character set; you still need `.rstrip(b"=")` yourself, since Python doesn't strip padding automatically and the PKCE spec requires unpadded output.
- **`jwt.decode` throws `DecodeError` immediately**: you likely forgot `algorithm="HS256"` on encode or `algorithms=["HS256"]` on decode (note singular vs. plural — a very easy typo), or you're passing the wrong secret (make sure `JWT_SECRET` is the same value on encode and decode; in a real deployment this would come from `.env`, not be hardcoded as shown above — treat this hardcoded value as a "day 2, fix before day 8" placeholder).
- **Consent redirect loses query parameters**: double check every hidden `<input>` in `consent.html` actually round-trips into the POST body — it's easy to forget one and get a confusing `KeyError` deep in the consent handler.
- **Token "works" in curl but the scope check always fails**: `scope.split()` vs `scope.split(" ")` — a raw `.split()` with no argument splits on _any_ whitespace and collapses repeated spaces, which is usually what you want for a space-separated scope string; but if you built the string with commas or a different separator somewhere, this silently produces one giant "scope" string that never matches. Print `claims["scope"]` and `claims["scope"].split()` while debugging to see exactly what you're comparing against.

---

Tomorrow: the MCP server — the actual tools an agent calls, gated by the `require_scope` dependency you just built.