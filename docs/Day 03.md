# Day 3 — MCP Server, Catalog/Cart Tools, Policy Middleware

**Date: Friday, August 28, 2026.**

Today you build the thing an AI agent actually calls: the MCP server. Yesterday's OAuth server answers "is this client allowed to act at all, and with what scope." Today answers "given a scoped, valid token, what specific _tools_ exist, and what guardrails (rate limits, spend caps, audit logging) wrap every single call to them."

## What you'll have by tonight

An MCP server mounted at `/agent/mcp`, exposing `search_products`, `get_product`, `create_cart`, and `update_cart` as callable tools, each requiring the right OAuth scope, each rate-limited, each writing an audit log row whether it succeeds or is rejected — plus a plain-HTML `/admin/audit` page to actually look at that log.

---

## Concepts

### 1. What MCP is, and why it's not "just another REST API"

**MCP (Model Context Protocol)** is a standard, introduced by Anthropic, for exposing tools, data, and prompts to an LLM-driven agent in a way any compliant client can discover and call — the same motivation as, say, USB standardizing how peripherals talk to computers, so you don't need a bespoke driver for every mouse. Before MCP, if you wanted an LLM agent to use your API, you'd typically hand-write a custom tool description for whichever agent framework you happened to be using (LangChain, a custom loop, etc.) — and that description would need updating any time your API changed, and wouldn't work with a _different_ agent framework without rewriting it again.

An MCP **server** exposes a set of **tools** — each with a name, a description (crucially, in natural language an LLM can read to decide _when_ to call it), and a JSON Schema describing its inputs and outputs. An MCP **client** — embedded in an agent framework — can connect to any MCP server, ask "what tools do you have," and get back a machine-readable+LLM-readable catalog it can hand to the model. This is precisely why Day 5's buyer agent, built on LangGraph, can consume your MCP server's tools via `langchain-mcp-adapters` with almost no glue code specific to _your_ server: MCP is the interoperability layer, exactly analogous to how your `.well-known/agent-commerce.json` from Day 1 is the interoperability layer for _discovering_ a merchant in the first place.

**"Streamable HTTP"** is one of MCP's transport options (as opposed to, e.g., stdio, used when the MCP server and client run as subprocesses of each other on the same machine). It means MCP messages travel over a regular HTTP connection, with support for the server streaming multiple messages back over time (useful for a tool call that takes a while, or for the server pushing unsolicited notifications) — think of it as similar in spirit to how a chat completion API can stream tokens back incrementally rather than only returning one full response at the end. This is the transport that lets your MCP server live at a normal HTTPS URL (`/agent/mcp`), reachable the same way any other route on your FastAPI app is, rather than requiring a special process-spawning setup.

**FastMCP** is a Python library (now folded into the official `mcp` SDK) that lets you define MCP tools the same way FastAPI lets you define HTTP routes — decorate a function, and the framework handles the protocol plumbing (schema generation from type hints, request/response framing) for you.

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("openstore-catalog")

@mcp.tool()
def search_products(query: str) -> list[dict]:
    """Search the merchant's product catalog by keyword."""
    ...
```

The **docstring becomes the tool description an LLM reads** to decide whether this tool is relevant to what it's trying to do — this isn't a comment for humans only, it's part of the interface. Write these descriptions the way you'd explain the tool to a new colleague, not the way you'd write an internal code comment.

### 2. Why "never trust an agent-supplied total" is the load-bearing sentence of this whole project

The plan states it as a flat rule: _"Never trust an agent-supplied total, price, or cart hash. Everything is recomputed from the DB snapshot."_ This is worth sitting with, because it's the single principle that makes the rest of the security architecture actually hold.

An LLM-driven agent can be wrong — through a bug in your code, a hallucination, a prompt injection (Day 7's failure-path demo #7 exploits exactly this), or a bad actor controlling the agent's inputs. If your `checkout_initiate` tool accepted a `total` field the agent computed and just... trusted it, then the entire signed-mandate architecture is theater: an attacker who controls the agent's tool-call arguments could request a mandate for ₹1 while adding ₹5,000 of products to the cart, and your server would happily sign a mandate saying the human approved ₹1. The mandate would be _cryptographically valid_ and _substantively fraudulent_.

The fix is structural, not a validation-layer patch: **the server never reads a total, price, or cart hash from the request. It reads a cart_id (or an explicit list of SKUs+quantities), looks up current prices from its own database/catalog, computes the total itself, computes the hash itself.** Whatever the agent _claims_ is simply discarded — or, better, never even accepted as a parameter in the first place for the fields that matter. You'll see this pattern concretely in `create_cart`/`update_cart` below: the tool signature takes `sku` and `qty` from the caller, but price is always looked up server-side.

### 3. Token buckets: rate limiting without an external dependency

A **token bucket** is a simple rate-limiting algorithm: imagine a bucket that holds up to `N` tokens, refilling at a fixed rate (say, 1 token every 2 seconds), that starts full. Every request consumes one token; if the bucket is empty, the request is rejected (or delayed) until it refills. This gives you both a _burst_ allowance (you can spend all `N` tokens quickly if they've accumulated) and a _sustained_ rate limit (you can't exceed the refill rate indefinitely) — a nicer behavior than a naive "max N requests per fixed time window" counter, which allows a burst right at a window boundary that effectively doubles the intended rate.

For this project, an in-memory implementation is entirely sufficient (no Redis needed — this isn't a multi-server production deployment):

```python
import time

class TokenBucket:
    def __init__(self, capacity: int, refill_rate_per_sec: float):
        self.capacity = capacity
        self.tokens = capacity
        self.refill_rate = refill_rate_per_sec
        self.last_refill = time.monotonic()

    def try_consume(self) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False
```

`time.monotonic()` rather than `time.time()` matters here: `monotonic()` is guaranteed never to go backward (immune to system clock adjustments, NTP corrections, or a user changing their system clock), which is exactly the property you want for measuring elapsed time for a rate limiter — using wall-clock time for this is a subtle bug source if the clock ever jumps.

The plan wants limits **keyed `(client_id, tool)`**, and **tighter on money-touching tools**: a separate `TokenBucket` instance per (client, tool) pair, stored in a dict, with `checkout_initiate`/`checkout_confirm` given a smaller capacity and slower refill than `search_products`.

### 4. Rolling windows for spend caps

A **spend cap** needs two checks: a **per-transaction cap** (this one checkout can't exceed ₹X) and a **rolling window cap** (this client can't exceed ₹Y total across, say, the last 24 hours — even split across many smaller transactions). The per-tx check is a simple comparison. The rolling window check requires summing recent spend:

```python
from datetime import datetime, timedelta
from sqlmodel import Session, select
from merchant.models import SpendLedgerEntry

def rolling_spend(session: Session, client_id: str, window_hours: int = 24) -> int:
    cutoff = datetime.utcnow() - timedelta(hours=window_hours)
    entries = session.exec(
        select(SpendLedgerEntry)
        .where(SpendLedgerEntry.client_id == client_id)
        .where(SpendLedgerEntry.created_at >= cutoff)
    ).all()
    return sum(e.amount_minor for e in entries)
```

`select(...)` here is SQLModel's query builder (inherited from SQLAlchemy) — it constructs a SQL `SELECT` statement from Python method chaining rather than a raw string, and `session.exec(...)` runs it, giving back model instances directly. This is the ORM payoff mentioned on Day 1: no hand-written `SELECT * FROM spend_ledger_entry WHERE client_id = ? AND created_at >= ?` string, no manual row-to-object mapping.

---

## Build

### Step 1 — Mount FastMCP at `/agent/mcp`

**What**: create the MCP server object and mount its ASGI app inside your existing FastAPI app. **Tool**: `mcp` SDK (`uv add mcp`). **Why**: covered above — one process, one port, both a normal REST-ish API and an MCP endpoint, sharing the same database session and catalog adapter.

`merchant/mcp_server.py`:

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("openstore-catalog", stateless_http=True)
```

`stateless_http=True` tells FastMCP not to assume a persistent session between calls (appropriate here, since each tool call re-authenticates via its own bearer token and re-reads from the database — there's no server-side conversation state to maintain between MCP calls).

In `merchant/app.py`, mount it:

```python
from merchant.mcp_server import mcp

app.mount("/agent/mcp", mcp.streamable_http_app())
```

`app.mount(...)` attaches an entire separate ASGI application under a path prefix — different from `include_router`, which merges routes into the _same_ app. You're mounting because FastMCP provides its own complete ASGI app (handling the MCP protocol's specific request/response framing), rather than a set of routes that plug into FastAPI's routing directly.

### Step 2 — Extracting the bearer token inside an MCP tool

**What**: since MCP tools aren't regular FastAPI routes, you can't use `Depends(require_scope(...))` the same way — you need to pull the `Authorization` header out of the raw request context FastMCP gives you. **Tool**: FastMCP's request-context access pattern. **Why**: every tool still needs the exact same scope check as an ordinary route — the transport differs, the security requirement doesn't.

```python
from mcp.server.fastmcp import Context
from merchant.oauth.routes import JWT_SECRET
import jwt as pyjwt
from fastapi import HTTPException

def get_claims_from_context(ctx: Context, required_scope: str) -> dict:
    request = ctx.request_context.request  # underlying Starlette Request
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = auth_header.removeprefix("Bearer ")
    try:
        claims = pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except pyjwt.InvalidTokenError as e:
        raise HTTPException(401, f"Invalid token: {e}")
    scopes = claims.get("scope", "").split()
    if required_scope not in scopes:
        raise HTTPException(403, f"Missing scope: {required_scope}")
    return claims
```

You'll call this as the first line of every tool below. It's slightly repetitive across tools, but explicit and easy to trace — the alternative (a decorator that wraps every tool) is a fine refactor once you have 6+ tools written and the pattern is proven; don't build that abstraction before you have the repetition in front of you to abstract from.

### Step 3 — Rate limiter and spend ledger as shared state

**What**: instantiate the token bucket registry and wire spend-cap checking, both importable by every tool. **Tool**: the `TokenBucket` class from Concepts, plus the `rolling_spend` query.

`merchant/policy.py`:

```python
import time
from merchant.models import SpendLedgerEntry
from sqlmodel import Session, select
from datetime import datetime, timedelta

class TokenBucket:
    def __init__(self, capacity: int, refill_rate_per_sec: float):
        self.capacity = capacity
        self.tokens = capacity
        self.refill_rate = refill_rate_per_sec
        self.last_refill = time.monotonic()

    def try_consume(self) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False

_BUCKETS: dict[tuple[str, str], TokenBucket] = {}

# tool_name -> (capacity, refill_per_sec). Money-touching tools get much tighter limits.
BUCKET_CONFIG = {
    "search_products": (20, 2.0),
    "get_product": (20, 2.0),
    "create_cart": (10, 1.0),
    "update_cart": (10, 1.0),
    "checkout_initiate": (3, 0.05),
    "get_signed_mandate": (3, 0.05),
    "checkout_confirm": (3, 0.05),
}

def check_rate_limit(client_id: str, tool: str) -> bool:
    key = (client_id, tool)
    if key not in _BUCKETS:
        capacity, refill = BUCKET_CONFIG.get(tool, (10, 1.0))
        _BUCKETS[key] = TokenBucket(capacity, refill)
    return _BUCKETS[key].try_consume()

def rolling_spend_minor(session: Session, client_id: str, window_hours: int = 24) -> int:
    cutoff = datetime.utcnow() - timedelta(hours=window_hours)
    entries = session.exec(
        select(SpendLedgerEntry)
        .where(SpendLedgerEntry.client_id == client_id)
        .where(SpendLedgerEntry.created_at >= cutoff)
    ).all()
    return sum(e.amount_minor for e in entries)
```

### Step 4 — Audit logging on every call, success or rejection

**What**: a single function that writes one `AuditLogEntry` row, called from every tool's entry and exit points. **Tool**: the `AuditLogEntry` table from Day 1, wrapped as a decorator so you don't have to remember to call it manually inside every tool body. **Why**: the plan's `/admin/audit` deliverable and Day 7's failure-path tests both depend on _every_ call being logged — success and rejection alike. A decorator guarantees this structurally instead of relying on you remembering it in each function.

```python
import time
import functools
import uuid
from merchant.db import engine
from sqlmodel import Session
from merchant.models import AuditLogEntry
from merchant.trace import emit
from fastapi import HTTPException

def audited_tool(tool_name: str):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            trace_id = str(uuid.uuid4())
            client_id = None
            try:
                result = fn(*args, trace_id=trace_id, **kwargs)
                client_id = kwargs.get("_client_id_for_audit")
                latency_ms = (time.perf_counter() - start) * 1000
                _write_audit(tool_name, client_id, kwargs, "ok", True, latency_ms, trace_id)
                return result
            except HTTPException as e:
                latency_ms = (time.perf_counter() - start) * 1000
                _write_audit(tool_name, client_id, kwargs, str(e.detail), False, latency_ms, trace_id)
                raise
        return wrapper
    return decorator

def _write_audit(tool, client_id, args, result_summary, success, latency_ms, trace_id):
    with Session(engine) as session:
        session.add(AuditLogEntry(
            trace_id=trace_id,
            client_id=client_id,
            tool=tool,
            args_json={k: v for k, v in args.items() if k != "ctx"},
            result_summary=result_summary[:200],
            success=success,
            latency_ms=latency_ms,
        ))
        session.commit()
    emit(
        "merchant-server",
        f"{tool} {'ok' if success else 'rejected'}",
        {"client": client_id or "unknown", "result": result_summary[:100]},
        trace_id,
        "executed" if success else "blocked",
    )
```

This is a slightly more advanced Python pattern than you've needed so far — a **decorator that wraps a function to add cross-cutting behavior** (logging, in this case) without that function's own body needing to know logging is happening. If decorators are new to you: `@audited_tool("search_products")` above a function definition is exactly equivalent to writing `search_products = audited_tool("search_products")(search_products)` after it — the decorator receives the function, and returns a _replacement_ function (`wrapper`) that does its own thing (timing, try/except, audit write) and then calls the original inside. `functools.wraps(fn)` preserves the original function's name and docstring on the wrapper — important here specifically because FastMCP reads the docstring to generate the tool's description for the LLM, so losing it to a naive wrapper would silently break tool discovery.

### Step 5 — The tools themselves

**What**: `search_products`, `get_product`, `create_cart`, `update_cart`. **Tool**: FastMCP's `@mcp.tool()`, the catalog adapter from Day 1, SQLModel for cart persistence. **Why**: these are the read/write primitives an agent needs before it can ever reach checkout — note none of them touch money or Razorpay; that's tomorrow, behind a much heavier verification ladder.

`merchant/mcp_server.py` (continuing):

```python
from mcp.server.fastmcp import FastMCP, Context
from merchant.catalog.yaml_adapter import YAMLCatalogAdapter
from merchant.config import settings
from merchant.db import engine
from merchant.models import Cart
from merchant.policy import check_rate_limit
from merchant.mcp_auth import get_claims_from_context
from merchant.audit import audited_tool
from sqlmodel import Session
from fastapi import HTTPException

mcp = FastMCP("openstore-catalog", stateless_http=True)
catalog = YAMLCatalogAdapter(settings.merchant_config_path)

@mcp.tool()
@audited_tool("search_products")
def search_products(query: str, ctx: Context, trace_id: str = None) -> list[dict]:
    """Search the merchant's product catalog by keyword. Returns matching products
    with sku, name, description, and price in minor units (paise)."""
    claims = get_claims_from_context(ctx, "catalog:read")
    if not check_rate_limit(claims["sub"], "search_products"):
        raise HTTPException(429, "Rate limit exceeded")
    results = catalog.search_products(query)
    return [p.model_dump() for p in results]

@mcp.tool()
@audited_tool("get_product")
def get_product(sku: str, ctx: Context, trace_id: str = None) -> dict:
    """Fetch full details for one product by its SKU."""
    claims = get_claims_from_context(ctx, "catalog:read")
    if not check_rate_limit(claims["sub"], "get_product"):
        raise HTTPException(429, "Rate limit exceeded")
    product = catalog.get_product(sku)
    if product is None:
        raise HTTPException(404, f"No product with sku {sku}")
    return product.model_dump()

@mcp.tool()
@audited_tool("create_cart")
def create_cart(items: list[dict], ctx: Context, trace_id: str = None) -> dict:
    """Create a new cart. items is a list of {sku, qty} — price is never taken
    from the caller, it is always looked up server-side from the current catalog."""
    claims = get_claims_from_context(ctx, "cart:write")
    if not check_rate_limit(claims["sub"], "create_cart"):
        raise HTTPException(429, "Rate limit exceeded")

    validated_items = _validate_and_price(items)
    with Session(engine) as session:
        cart = Cart(client_id=claims["sub"], version=1, items_json=validated_items)
        session.add(cart)
        session.commit()
        session.refresh(cart)
        return {"cart_id": cart.id, "version": cart.version, "items": cart.items_json}

@mcp.tool()
@audited_tool("update_cart")
def update_cart(cart_id: int, items: list[dict], ctx: Context, trace_id: str = None) -> dict:
    """Replace a cart's items. Bumps cart_version on every mutation — an old
    cart_version referenced anywhere downstream (e.g. a stale mandate) is invalid."""
    claims = get_claims_from_context(ctx, "cart:write")
    if not check_rate_limit(claims["sub"], "update_cart"):
        raise HTTPException(429, "Rate limit exceeded")

    validated_items = _validate_and_price(items)
    with Session(engine) as session:
        cart = session.get(Cart, cart_id)
        if cart is None or cart.client_id != claims["sub"]:
            raise HTTPException(404, "Cart not found")
        cart.items_json = validated_items
        cart.version += 1
        session.add(cart)
        session.commit()
        session.refresh(cart)
        return {"cart_id": cart.id, "version": cart.version, "items": cart.items_json}

def _validate_and_price(items: list[dict]) -> list[dict]:
    """Re-derives price for every item from the live catalog. This is the
    concrete implementation of 'never trust an agent-supplied total' from
    the cart layer: only sku and qty come from the caller."""
    priced = []
    for item in items:
        product = catalog.get_product(item["sku"])
        if product is None:
            raise HTTPException(400, f"Unknown sku: {item['sku']}")
        priced.append({
            "sku": product.sku,
            "qty": item["qty"],
            "unit_minor": product.price_minor,  # NEVER item.get("price") or similar
        })
    return priced
```

`_validate_and_price` is the concrete implementation of the "never trust an agent-supplied total" principle from Concepts — read it again and confirm to yourself that there is no code path where a caller-supplied price ever makes it into `validated_items`. This function is one of the five things the plan says to _never_ cut, in spirit if not by name (it's the mechanism behind "the signed mandate" being trustworthy at all).

`session.refresh(cart)` after `commit()` reloads the object's fields from the database — necessary here because `cart.id` is `None` in Python memory until the database actually assigns it on insert; `refresh` pulls that assigned value back in so you can return it.

### Step 6 — `GET /admin/audit`: the human-readable audit trail

**What**: a plain HTML table listing recent audit log entries. **Tool**: Jinja2, same as the storefront. **Why**: the plan calls this "the human-readable audit trail deliverable" — distinct from the Discord `#audit-trail` channel (which is a live stream), this is a queryable, at-a-glance view a merchant could actually use to answer "what has this agent done."

```python
from sqlmodel import select
from merchant.models import AuditLogEntry

@app.get("/admin/audit", response_class=HTMLResponse)
def admin_audit(request: Request, session: Session = Depends(get_session)):
    entries = session.exec(
        select(AuditLogEntry).order_by(AuditLogEntry.created_at.desc()).limit(100)
    ).all()
    return templates.TemplateResponse(
        "audit.html", {"request": request, "entries": entries}
    )
```

`merchant/storefront/audit.html` (reusing the same templates directory is fine for now):

```html
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Audit Log</title></head>
<body>
<h1>Audit Log (last 100)</h1>
<table border="1" cellpadding="4">
  <tr><th>Time</th><th>Client</th><th>Tool</th><th>Success</th><th>Latency (ms)</th><th>Result</th></tr>
  {% for e in entries %}
  <tr style="background: {{ 'lightgreen' if e.success else 'salmon' }}">
    <td>{{ e.created_at }}</td>
    <td>{{ e.client_id }}</td>
    <td>{{ e.tool }}</td>
    <td>{{ e.success }}</td>
    <td>{{ "%.1f"|format(e.latency_ms) }}</td>
    <td>{{ e.result_summary }}</td>
  </tr>
  {% endfor %}
</table>
</body>
</html>
```

---

## Exit check (from the plan)

> ✅ Exit: `mcp` inspector or a raw httpx script lists and calls tools with a real token; unscoped call is cleanly rejected and appears in `#merchant-server` and the audit table.

Two things to actually verify, not just one:

1. **Tool discovery and a successful call.** The `mcp` package ships an inspector CLI you can point at your running server, or write a short script using an MCP client library. Either way: connect to `http://localhost:8000/agent/mcp`, confirm `search_products`, `get_product`, `create_cart`, `update_cart` are all listed with their descriptions and schemas, then call `search_products` with a valid `catalog:read`-scoped token (get one via the full Day 2 flow) and confirm you get real product results back.
2. **An unscoped call is cleanly rejected, and it's visible in two places.** Call `create_cart` (needs `cart:write`) using a token you deliberately requested with _only_ `catalog:read` scope. Confirm: (a) you get a 403, not a 500 or a silent success; (b) a red "blocked" embed appears in `#merchant-server`; (c) a row with `success=False` appears at `/admin/audit`.

If either check silently "just works without you noticing a rejection," go back — a checkout system where you can't tell the difference between "this succeeded" and "this was correctly blocked" is worse than one with no checks at all, because it hides exactly the failure mode you're trying to build confidence against.

## What could go wrong

- **FastMCP tool calls can't find the `Authorization` header**: depending on your `mcp` SDK version, `ctx.request_context.request` may live at a slightly different attribute path — if this breaks, print `dir(ctx.request_context)` and `dir(ctx.request_context.request)` to find the right attribute rather than guessing; MCP's Python SDK has moved fields between versions.
- **Rate limiting seems to never trigger during testing**: remember the bucket refills continuously — if you're testing with deliberate pauses between calls (e.g., manually clicking through curl commands), you may never actually exhaust the bucket. Write a tight loop (`for _ in range(30): check_rate_limit(...)`) to actually see it flip to `False`.
- **`_validate_and_price` silently "succeeds" with a wrong price**: if you ever see a price in a cart that doesn't match `gelateria.yaml`, you have a code path somewhere reading price from the request instead of the catalog — treat this as a stop-everything bug, not a minor one, given what this function protects.
- **Audit rows show `client_id: None`** even on successful calls: check that `_write_audit`'s `client_id` extraction actually has access to the claims — the decorator pattern above passes `client_id` awkwardly through `kwargs.get("_client_id_for_audit")`, which nothing currently sets. Fix this by having each tool's wrapper capture `claims["sub"]` after calling `get_claims_from_context` and stash it somewhere the decorator can read (e.g., have `get_claims_from_context` write to a `contextvars.ContextVar`, or simplify by moving the audit write to happen explicitly inside each tool rather than in the decorator — either is a reasonable Day 3 judgment call; don't let the decorator's elegance cost you correctness here).

---

Tomorrow is the highest-risk day in the whole project: checkout, OTP, the Ed25519 mandate, real Razorpay calls, and webhooks. Protect the time.