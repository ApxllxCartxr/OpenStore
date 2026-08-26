				# Day 1 — Foundations, Contracts, Traces

**Date: Wednesday, August 26, 2026.**

Today you're not writing business logic. You're pouring the foundation: the web framework, the database, the data models everything else imports, the catalog, the storefront page, and — critically — the observability system that every future day logs through. Nothing you build today does anything impressive on its own. But every single later day depends on today being solid, so go slow.

## What you'll have by tonight

A FastAPI server that serves a storefront page for a fictional gelato shop, a `/.well-known/agent-commerce.json` file describing how an AI agent could shop there, and a working pipeline that posts a message into a real Discord channel every time something happens on the server — the "trace" system you'll use to *see* your system think, for the entire rest of the project.

---

## Concepts

### 1. What FastAPI actually is, and what ASGI means

You know `discord.py` — you register handlers for events (`on_message`, a slash command), and a runtime calls them when something happens. A web framework is the same shape, except the "event" is an incoming HTTP request, and the "runtime" is a web server.

**FastAPI** is a Python web framework. You write functions, decorate them with the HTTP method and path they should handle, and FastAPI wires them up:

```python
from fastapi import FastAPI

app = FastAPI()

@app.get("/hello")
def say_hello():
    return {"message": "hi"}
```

Run this, visit `http://localhost:8000/hello`, and you get back `{"message": "hi"}` as JSON. FastAPI turned your Python dict into a JSON HTTP response automatically. That's the whole basic idea: Python function in, HTTP endpoint out.

**ASGI** (Asynchronous Server Gateway Interface) is the *contract* between a web framework like FastAPI and the actual program that talks to the network — the "server" in "web server." FastAPI itself doesn't listen on a port; it just knows how to respond to a request once one arrives. **Uvicorn** is the ASGI server: it's the program that actually binds to port 8000, accepts TCP connections, parses raw HTTP, and calls into FastAPI to get a response. You'll run your app as:

```bash
uvicorn merchant.app:app --reload --port 8000
```

That says: "Take the ASGI application named `app` inside the Python module `merchant/app.py`, and serve it." `--reload` restarts the server whenever you save a file — essential during development, never used in production. This is directly analogous to how `discord.py`'s `bot.run(TOKEN)` starts an event loop that listens for Discord gateway events and dispatches them to your handlers — Uvicorn's loop listens for HTTP connections and dispatches them to FastAPI's routes.

"Asynchronous" matters because a real server handles many requests concurrently. If your handler for one request is waiting on a slow database query, the server shouldn't freeze for every other user. You've already touched this in `discord.py` — every event handler is `async def`, and you `await` things like sending a message. Same discipline here: FastAPI route functions can be `async def`, and inside them you `await` database calls, HTTP calls to Razorpay, etc., so one slow request doesn't block the others.

### 2. What Pydantic is, and why "contracts" come first

**Pydantic** is a Python library for defining the *shape* of data as a class, and getting free validation, parsing, and serialization. You declare a class like this:

```python
from pydantic import BaseModel

class Product(BaseModel):
    sku: str
    name: str
    price_minor: int
```

Now `Product(sku="gel-001", name="Pistachio", price_minor=25000)` is a real Python object with type-checked fields. If you tried `Product(sku="gel-001", name="Pistachio", price_minor="expensive")`, Pydantic raises a `ValidationError` immediately, because `"expensive"` isn't an `int`. This is the mechanism FastAPI uses to validate request bodies and serialize response bodies — you almost never write JSON-parsing code by hand.

Why does the plan say "freeze contracts as Pydantic models on Day 1, before anything else"? Because in a multi-process system — and OpenStore is *three* separate Python processes (merchant server, merchant reasoning agent, buyer agent) that don't share memory — the only thing binding them together is the **shape of the messages they send each other**. If the merchant server's idea of "what a cart looks like" drifts from the buyer agent's idea of "what a cart looks like" even slightly, you get bugs that are miserable to trace, because each process looks correct in isolation. Fixing the shape of `agent-commerce.json`, the mandate payload, and the checkout state machine *before* writing the logic that uses them means every later day is building against a contract that can't silently drift.

### 3. What an ORM is, and why SQLModel specifically

You need to persist data — products, carts, orders — across restarts, which means a database. **SQLite** is a database engine that stores everything in a single file on disk (no separate database server process to run, unlike Postgres or MySQL) — perfect for a project like this. You *could* write raw SQL strings and execute them, but that's error-prone and verbose for anything with more than a couple of tables.

An **ORM** (Object-Relational Mapper) lets you define your database tables as Python classes, and query/insert/update using Python objects and method calls instead of hand-written SQL strings. **SQLModel** (by the same author as FastAPI, Sebastián Ramírez) is specifically designed to *also* be a Pydantic model — meaning the same class can define your database table schema *and* your API's request/response shape. That's exactly why the plan picked it: you get the FastAPI validation benefits and the database schema in one declaration, instead of maintaining two parallel sets of classes that have to be kept in sync by hand.

```python
from sqlmodel import SQLModel, Field
from typing import Optional

class Product(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    sku: str = Field(unique=True, index=True)
    name: str
    price_minor: int
```

`table=True` tells SQLModel this class is an actual database table, not just a validation schema. `Field(primary_key=True)` and `Field(unique=True, index=True)` map directly to SQL column constraints.

**WAL mode** (Write-Ahead Logging) is a SQLite setting that changes how it handles concurrent access. By default, SQLite locks the *entire* database file during a write, which means readers have to wait. WAL mode lets reads happen concurrently with a write, by writing changes to a separate log file first and merging them into the main database later. You need this because OpenStore will have multiple concurrent operations hitting the database — a webhook arriving while an MCP tool call reads the catalog, for instance. Without WAL, you'd get "database is locked" errors under any real concurrency, which is exactly the kind of thing that looks fine on your machine and breaks during a live demo with real timing.

### 4. Minor units — and why prices are integers, never floats

Notice `price_minor: int`, not `price: float`. Money is stored in **minor units** — for INR, that's paise (1 rupee = 100 paise) — as an integer, everywhere, always. Never a float.

This isn't pedantry. Floating-point numbers cannot exactly represent most decimal fractions (0.1 in binary floating point is actually a repeating fraction, same reason 1/3 has no exact decimal representation). Do enough arithmetic on float-based currency and you accumulate tiny rounding errors that eventually show up as a mismatch between what your server computed and what Razorpay charged — and in a system whose entire thesis is "the total is cryptographically bound to what the human approved," a rounding-error mismatch is a security bug, not a cosmetic one. ₹250.00 is stored and computed as the integer `25000`. You only ever divide by 100 and add a currency symbol at the very last step, when rendering something for a human to read.

### 5. The Protocol / Adapter pattern

The plan wants a `CatalogAdapter` "protocol," with a `YAMLAdapter` as the one real implementation. A **Protocol** in Python (from the `typing` module) defines a *shape of behavior* — a set of method signatures — without requiring implementers to inherit from a base class. It's Python's version of an "interface" from languages like Java or Go.

```python
from typing import Protocol

class CatalogAdapter(Protocol):
    def get_product(self, sku: str) -> Product | None: ...
    def search_products(self, query: str) -> list[Product]: ...
```

Any class with methods matching those signatures satisfies this Protocol — no inheritance needed (this is called "structural typing" or "duck typing with type-checker support"). The entire *point* of this pattern here is the plan's genericity claim from Day 8: "swap the catalog adapter, and the same server works for a completely different merchant." If your checkout logic, MCP tools, and storefront rendering all call `catalog_adapter.get_product(sku)` rather than reading a YAML file directly wherever they need product data, then on Day 8 you prove genericity by writing a *second* YAML config and pointing the same adapter at it — zero code changes to anything downstream. If you skip this pattern and just scatter `yaml.safe_load(...)` calls through your checkout code, Day 8's "same code, different merchant" demo becomes a lie you'd have to fake.

### 6. JSON-LD and `schema.org` — machine-readable web pages

Normally a web page is meant for a human to read; a browser renders HTML and a person interprets it. **`schema.org`** is a shared vocabulary (maintained jointly by Google, Microsoft, Yahoo, and Yandex) for describing *what a page means*, not just how it looks — "this is a `Product`, its `name` is X, its `Offer` has `price` Y." **JSON-LD** ("JSON for Linked Data") is one way to embed that meaning into a page: a `<script type="application/ld+json">` block containing structured JSON that search engines (and, in our case, AI shopping agents) can parse directly, instead of having to guess meaning from HTML layout.

```html
<script type="application/ld+json">
{
  "@context": "https://schema.org/",
  "@type": "Product",
  "name": "Pistachio Gelato (500ml)",
  "sku": "gel-001",
  "offers": {
    "@type": "Offer",
    "price": "250.00",
    "priceCurrency": "INR"
  }
}
</script>
```

Why this matters for OpenStore specifically: your storefront page needs to be legible to *both* a human browsing it and an AI buyer agent parsing it, without the AI agent needing a special API just to know what's for sale. This JSON-LD block is the storefront's contribution to that. Google has a free ["Rich Results Test"](https://search.google.com/test/rich-results) that validates whether your JSON-LD is well-formed and correctly typed — you'll use this as your Day 1 sanity check instead of guessing.

### 7. Discord Webhooks are not a bot

You've used `discord.py`, which connects as a **bot** — a persistent WebSocket connection that both receives events (messages, reactions) and can take any action the bot's permissions allow. A **Discord webhook**, by contrast, is a single URL, tied to one specific channel, that accepts an HTTP POST and posts a message *as a fixed identity* into that channel. It's one-directional (you can only send, never receive) and requires no bot process, no gateway connection, no token scopes — just an HTTP POST to a URL.

```python
import httpx

def emit_to_discord(webhook_url: str, content: str):
    httpx.post(webhook_url, json={"content": content})
```

Why webhooks for the trace system, instead of just using one of your bots to send the message? Because the trace system needs to be simple, synchronous-feeling, and callable from *any* of your three processes without each of them needing a full Discord bot connection just to log a line. A webhook URL is a plain string you paste into `.env` and POST to from anywhere — merchant server, reasoning agent, or buyer agent — with a single HTTP client call. **`httpx`** is the HTTP client library you'll use for this (and for talking to Razorpay, and anywhere else you need to make an HTTP request from Python) — think of it as the `requests`-library successor with native `async` support, which matters once you're calling it from inside an `async def` FastAPI route.

A Discord **embed** is a richly-formatted message block (colored left border, title, fields, footer) — you've likely built these in `discord.py` via `discord.Embed(...)`. Webhooks accept the same embed structure as raw JSON in the POST body, which is what lets your trace system produce the color-coded, structured messages the plan wants (blue=info, amber=gate, red=blocked, green=executed — you'll apply the actual color scheme on Day 8, but build the plumbing for it now).

---

## Build

### Step 1 — Install `uv` and scaffold the project

**What**: set up a Python project with a dependency manager.
**Tool**: `uv`, a modern, very fast Python package and project manager (replaces the older `pip` + `venv` + `pip-tools` combination with one tool).
**Why**: `uv` gives you reproducible, locked dependencies and a fast, no-fuss virtual environment — one less thing to fight with while you're trying to focus on the actual architecture.

```bash
mkdir openstore && cd openstore
uv init --name openstore
uv add fastapi uvicorn sqlmodel pydantic-settings jinja2 cryptography pyjwt razorpay httpx python-dotenv
```

`uv add` installs each package and pins its version in a `pyproject.toml`/`uv.lock` pair, so re-running `uv sync` on another machine reproduces the exact same environment. Quick rundown of what each dependency is for (some you already met above):

| Package | Purpose |
|---|---|
| `fastapi` | the web framework |
| `uvicorn` | the ASGI server that runs it |
| `sqlmodel` | ORM + Pydantic-in-one, for the database |
| `pydantic-settings` | loads config (like `.env` values) into a validated Pydantic model |
| `jinja2` | templating engine, for rendering the storefront HTML page |
| `cryptography` | you'll use this on Day 4 for Ed25519 signing |
| `pyjwt` | JWT encode/decode, used in OAuth (Day 2) and the mandate (Day 4) |
| `razorpay` | official Razorpay Python SDK |
| `httpx` | HTTP client — Discord webhooks, Razorpay calls where the SDK doesn't cover it |
| `python-dotenv` | loads a `.env` file's contents into environment variables |

Now create the repo layout from the plan:

```bash
mkdir -p merchant/catalog merchant/oauth merchant/storefront
mkdir -p merchant_agent/skills
mkdir -p buyer_agent
mkdir -p config tests
touch merchant/__init__.py merchant/app.py merchant/config.py merchant/db.py merchant/models.py
touch merchant/catalog/__init__.py merchant/catalog/adapter.py merchant/catalog/yaml_adapter.py
touch merchant/trace.py
touch .env.example .gitignore README.md
```

You won't touch `merchant_agent/` or `buyer_agent/` until Days 5–6, but scaffolding the folders now means the repo layout matches the architecture from day one — nobody "discovers" the folder structure later by accident.

### Step 2 — `.env` and settings

**What**: a place for secrets and per-environment config (Discord webhook URLs, Razorpay keys) that never gets committed to git.
**Tool**: `python-dotenv` to load `.env`, `pydantic-settings` to validate it into a typed object.
**Why**: hardcoding secrets in source files is how they end up in git history forever. A `.env` file, `.gitignore`'d, is the standard fix.

`.gitignore`:
```
.env
*.db
*.db-wal
*.db-shm
__pycache__/
.venv/
```

`.env.example` (commit this one — it documents *which* variables are needed, with no real values):
```
DISCORD_WEBHOOK_BUYER_AGENT=
DISCORD_WEBHOOK_MERCHANT_AGENT=
DISCORD_WEBHOOK_MERCHANT_SERVER=
DISCORD_WEBHOOK_AUDIT_TRAIL=
DATABASE_URL=sqlite:///./openstore.db
MERCHANT_CONFIG_PATH=config/gelateria.yaml
```

Copy it to a real `.env` (not committed) and fill in the four webhook URLs from your Discord channels once you've created them (see the prerequisites checklist in `00-START-HERE.md`).

`merchant/config.py`:
```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    discord_webhook_buyer_agent: str
    discord_webhook_merchant_agent: str
    discord_webhook_merchant_server: str
    discord_webhook_audit_trail: str
    database_url: str = "sqlite:///./openstore.db"
    merchant_config_path: str = "config/gelateria.yaml"

    class Config:
        env_file = ".env"

settings = Settings()
```

`BaseSettings` (from `pydantic-settings`) is a Pydantic model that automatically populates its fields from environment variables (case-insensitively matched to field names) and, via `Config.env_file`, from a `.env` file. Import `settings` anywhere you need a config value — you now have one validated source of truth instead of `os.environ.get(...)` calls scattered everywhere, each of which could typo the variable name with no error until runtime.

### Step 3 — Freeze the three contracts as Pydantic models

**What**: turn the three JSON shapes from the plan's §3 (the `.well-known` document, the mandate payload, the checkout states) into real Pydantic models, plus a committed JSON fixture for the well-known document.
**Tool**: Pydantic `BaseModel` and Python's `enum.Enum`.
**Why**: covered in Concepts above — this is the shape every later process imports and agrees on.

Add to `merchant/models.py` (contracts only for now — table models come in Step 4):

```python
from enum import Enum
from pydantic import BaseModel

class CheckoutStatus(str, Enum):
    PENDING = "PENDING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    MANDATE_ISSUED = "MANDATE_ISSUED"
    ORDER_CREATED = "ORDER_CREATED"
    REJECTED = "REJECTED"
    PAID = "PAID"
    FAILED = "FAILED"

class AuthDescriptor(BaseModel):
    type: str = "oauth2"
    authorization_server: str
    scopes_supported: list[str]

class PolicyDescriptor(BaseModel):
    currency: str
    max_unconfirmed_spend_minor: int
    requires_human_approval: bool
    default_per_tx_cap_minor: int

class MerchantDescriptor(BaseModel):
    name: str
    id: str

class AgentCommerceDescriptor(BaseModel):
    version: str = "0.1"
    merchant: MerchantDescriptor
    storefront: str
    catalog_endpoint: str
    mcp_endpoint: str
    a2a_agent_card: str
    auth: AuthDescriptor
    policy: PolicyDescriptor

class MandateCart(BaseModel):
    hash: str
    version: int
    items: list[dict]  # {sku, qty, unit_minor} — tightened once checkout.py exists

class MandatePayload(BaseModel):
    iss: str          # merchant id
    jti: str          # mandate id, single-use
    aud: str = "openstore-mcp"
    chk: str          # checkout_id
    sub: str          # oauth client_id
    cart: MandateCart
    iat: int
    exp: int
    amt: int          # total, minor units
    cur: str = "INR"
    nonce: str
    dlv: str          # sha256 of canonical delivery address
```

`str, Enum` (rather than plain `Enum`) means each `CheckoutStatus` member *is* a string at runtime — `CheckoutStatus.PENDING == "PENDING"` is `True`. This matters because FastAPI and SQLModel serialize/store it as a plain string without extra configuration.

Now generate the committed JSON fixture — this is literally the example from the plan, saved as a file everyone (including your future self, and any test) can load and compare against:

```bash
mkdir -p tests/fixtures
```

`tests/fixtures/agent_commerce.json`:
```json
{
  "version": "0.1",
  "merchant": { "name": "Gelateria Roma", "id": "gelateria-roma" },
  "storefront": "http://localhost:8000/",
  "catalog_endpoint": "http://localhost:8000/agent/catalog",
  "mcp_endpoint": "http://localhost:8000/agent/mcp",
  "a2a_agent_card": "http://localhost:8001/.well-known/agent-card.json",
  "auth": {
    "type": "oauth2",
    "authorization_server": "http://localhost:8000/.well-known/oauth-authorization-server",
    "scopes_supported": ["catalog:read", "cart:write", "checkout:initiate", "checkout:confirm"]
  },
  "policy": {
    "currency": "INR",
    "max_unconfirmed_spend_minor": 0,
    "requires_human_approval": true,
    "default_per_tx_cap_minor": 50000
  }
}
```

You're using `localhost` URLs for now since the tunnel doesn't exist until Day 4 — swap in real URLs once you have them.

### Step 4 — Database schema and WAL mode

**What**: the SQLModel table classes for the whole system (yes, all of them, now — even ones you won't populate until Day 4 or later. Freezing the schema shape early avoids incompatible migrations mid-project).
**Tool**: SQLModel, SQLite.
**Why**: covered above.

`merchant/db.py`:
```python
from sqlmodel import SQLModel, create_engine, Session
from merchant.config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
)

def init_db():
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session
```

`PRAGMA journal_mode=WAL` is the literal SQLite command that turns on WAL mode — issued once against the raw connection. `check_same_thread=False` is needed because SQLite's default Python driver assumes only the thread that opened a connection will use it; FastAPI may run your route on a different thread from the one that created the engine, so you relax that check (safe here because SQLModel's `Session` still ensures one session per request, not concurrent use of the same session object).

`SQLModel.metadata.create_all(engine)` walks every `SQLModel` subclass with `table=True` that's been imported anywhere in your process, and issues `CREATE TABLE IF NOT EXISTS` for each. This is why `merchant/models.py` needs to be imported before you call `init_db()` — Python only registers a class with SQLModel's metadata when the module defining it has actually been executed/imported.

`get_session` is a **FastAPI dependency** — a function that FastAPI calls automatically to produce a value your route function needs, injected as an argument. You'll see this pattern constantly: `def my_route(session: Session = Depends(get_session))`. The `yield` (rather than `return`) means FastAPI can run cleanup code after the route finishes — here, the `with Session(engine) as session:` block closes the session automatically once the generator resumes after `yield`.

Now add the full table schema to `merchant/models.py`, below the contracts from Step 3:

```python
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field, JSON, Column

class Product(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    sku: str = Field(unique=True, index=True)
    name: str
    description: str
    price_minor: int
    currency: str = "INR"
    related_skus: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))

class Cart(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: str = Field(index=True)
    version: int = Field(default=1)
    items_json: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class Checkout(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    checkout_id: str = Field(unique=True, index=True)
    cart_id: int = Field(foreign_key="cart.id")
    client_id: str = Field(index=True)
    status: str = Field(default="PENDING")
    cart_hash: str
    total_minor: int
    delivery_address: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime

class OTPChallenge(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    checkout_id: str = Field(index=True)
    otp_hash: str
    attempts: int = Field(default=0)
    used: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Mandate(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    jti: str = Field(unique=True, index=True)
    checkout_id: str = Field(index=True)
    jws_compact: str
    fingerprint: str
    burned: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Order(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    checkout_id: str = Field(index=True)
    razorpay_order_id: Optional[str] = None
    razorpay_payment_link_id: Optional[str] = None
    status: str = Field(default="CREATED")
    total_minor: int
    created_at: datetime = Field(default_factory=datetime.utcnow)

class WebhookEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    razorpay_event_id: str = Field(unique=True, index=True)
    event_type: str
    payload_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    processed_at: datetime = Field(default_factory=datetime.utcnow)

class OAuthClient(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: str = Field(unique=True, index=True)
    client_secret_hash: str
    display_name: str
    redirect_uris: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    active: bool = Field(default=True)

class OAuthToken(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    jti: str = Field(unique=True, index=True)
    client_id: str = Field(index=True)
    scopes: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    expires_at: datetime
    revoked: bool = Field(default=False)

class SpendLedgerEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: str = Field(index=True)
    amount_minor: int
    created_at: datetime = Field(default_factory=datetime.utcnow)

class AuditLogEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    trace_id: str = Field(index=True)
    client_id: Optional[str] = None
    tool: str
    args_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    result_summary: str
    success: bool
    latency_ms: float
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

A few notes on things that look unusual if this is your first ORM:

- `Field(sa_column=Column(JSON))` is how you store a Python `list` or `dict` in a column — SQLite doesn't have a native array/object type, so SQLModel (via SQLAlchemy underneath) serializes it to a JSON text column and deserializes it back into a Python object on read, transparently.
- `foreign_key="cart.id"` on `Checkout.cart_id` declares a relational link — SQLite will refuse to insert a `Checkout` whose `cart_id` doesn't correspond to an existing `Cart.id` (once foreign key enforcement is on; SQLite requires `PRAGMA foreign_keys=ON`, add that alongside the WAL pragma in `init_db` if you want this enforced).
- Every table has an `id: Optional[int] = Field(default=None, primary_key=True)`. It's `Optional` because before the row is inserted, there is no ID yet — SQLite assigns it on insert. This is a repeating pattern; you'll type it on every table.

You don't need every field's exact purpose crystal clear yet — several tables (OTP, Mandate, OAuthToken) aren't touched by code until Day 2 or Day 4. Freezing the schema now means Day 4 doesn't also become "redesign the database" day.

### Step 5 — Catalog adapter protocol + YAML implementation

**What**: the `CatalogAdapter` protocol from Concepts, plus a real implementation that reads a YAML config file.
**Tool**: `typing.Protocol`, PyYAML (add it: `uv add pyyaml`).
**Why**: covered above — this is what makes Day 8's "second merchant, zero code changes" claim true instead of aspirational.

`merchant/catalog/adapter.py`:
```python
from typing import Protocol
from merchant.models import Product

class CatalogAdapter(Protocol):
    def get_product(self, sku: str) -> Product | None: ...
    def search_products(self, query: str) -> list[Product]: ...
    def list_all(self) -> list[Product]: ...
```

`merchant/catalog/yaml_adapter.py`:
```python
import yaml
from merchant.models import Product
from merchant.catalog.adapter import CatalogAdapter

class YAMLCatalogAdapter(CatalogAdapter):
    def __init__(self, config_path: str):
        with open(config_path) as f:
            raw = yaml.safe_load(f)
        self._products: dict[str, Product] = {
            p["sku"]: Product(**p) for p in raw["products"]
        }

    def get_product(self, sku: str) -> Product | None:
        return self._products.get(sku)

    def search_products(self, query: str) -> list[Product]:
        q = query.lower()
        return [
            p for p in self._products.values()
            if q in p.name.lower() or q in p.description.lower()
        ]

    def list_all(self) -> list[Product]:
        return list(self._products.values())
```

Note this adapter builds `Product` Pydantic objects (not database rows) from YAML directly — the catalog here is config-driven, not database-driven; the *database* `Product` table (Step 4) exists for future flexibility, but for now the source of truth is the YAML file, matching the plan's "a merchant swaps this file" model. `Product(**p)` unpacks each YAML dict's keys as keyword arguments into the Pydantic model, which validates them on construction — if your YAML has a typo like `pric_minor` instead of `price_minor`, this throws immediately and tells you exactly which field, rather than failing silently or crashing deep inside checkout logic later.

Now seed the actual catalog. `config/gelateria.yaml`:
```yaml
merchant:
  name: "Gelateria Roma"
  id: "gelateria-roma"

products:
  - sku: "gel-001"
    name: "Pistachio Gelato (500ml)"
    description: "Sicilian pistachio, no artificial color."
    price_minor: 25000
    related_skus: ["gel-002", "top-001"]
    tags: ["nuts", "classic", "gift"]

  - sku: "gel-002"
    name: "Stracciatella Gelato (500ml)"
    description: "Sweet cream base with dark chocolate shavings."
    price_minor: 24000
    related_skus: ["gel-001", "top-002"]
    tags: ["chocolate", "classic"]

  - sku: "gel-003"
    name: "Mango Sorbetto (500ml)"
    description: "Dairy-free, Alphonso mango."
    price_minor: 26000
    related_skus: ["gel-004"]
    tags: ["fruit", "dairy-free", "vegan"]

  - sku: "gel-004"
    name: "Raspberry Sorbetto (500ml)"
    description: "Dairy-free, tart and bright."
    price_minor: 26000
    related_skus: ["gel-003"]
    tags: ["fruit", "dairy-free", "vegan"]

  - sku: "gel-005"
    name: "Tiramisu Gelato (500ml)"
    description: "Espresso-soaked ladyfinger swirl."
    price_minor: 27000
    related_skus: ["top-002"]
    tags: ["coffee", "gift", "occasion:birthday"]

  - sku: "gel-006"
    name: "Hazelnut Gelato (500ml)"
    description: "Piedmont hazelnut, rich and smooth."
    price_minor: 25500
    related_skus: ["gel-001"]
    tags: ["nuts", "classic"]

  - sku: "gel-007"
    name: "Lemon Sorbetto (500ml)"
    description: "Amalfi lemon, dairy-free."
    price_minor: 23000
    related_skus: ["gel-003"]
    tags: ["fruit", "dairy-free", "vegan"]

  - sku: "top-001"
    name: "Toasted Pistachio Topping (100g)"
    description: "Crushed toasted pistachio, add-on."
    price_minor: 9000
    related_skus: ["gel-001"]
    tags: ["nuts", "topping"]

  - sku: "top-002"
    name: "Dark Chocolate Shavings (100g)"
    description: "70% cocoa shavings, add-on."
    price_minor: 8000
    related_skus: ["gel-002", "gel-005"]
    tags: ["chocolate", "topping"]

  - sku: "box-001"
    name: "Gift Box (holds 2 pints)"
    description: "Insulated gift packaging, holds two 500ml tubs."
    price_minor: 15000
    related_skus: []
    tags: ["gift", "occasion:birthday", "occasion:anniversary"]
```

Ten products, `related_skus` for cross-sell, `tags` including occasion/dietary markers you'll use on Day 6's cross-sell skill, prices in paise. This matches the plan's requirement exactly.

### Step 6 — Storefront page with JSON-LD

**What**: a single Jinja-templated HTML page listing the catalog, with embedded `schema.org` JSON-LD per product.
**Tool**: Jinja2 (already installed), FastAPI's `Jinja2Templates`.
**Why**: covered in Concepts — legible to both a human browser and an AI agent parsing structured data.

`merchant/storefront/index.html`:
```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{{ merchant_name }}</title>
</head>
<body>
  <h1>{{ merchant_name }}</h1>
  <ul>
  {% for product in products %}
    <li>
      <h2>{{ product.name }}</h2>
      <p>{{ product.description }}</p>
      <p>₹{{ "%.2f"|format(product.price_minor / 100) }}</p>
      <script type="application/ld+json">
      {
        "@context": "https://schema.org/",
        "@type": "Product",
        "name": {{ product.name|tojson }},
        "sku": {{ product.sku|tojson }},
        "description": {{ product.description|tojson }},
        "offers": {
          "@type": "Offer",
          "price": "{{ "%.2f"|format(product.price_minor / 100) }}",
          "priceCurrency": "INR"
        }
      }
      </script>
    </li>
  {% endfor %}
  </ul>
</body>
</html>
```

`{{ product.price_minor / 100 }}` is the *only* place a division-into-decimal happens — purely for display, never for a computation that feeds back into the system. `|tojson` is a Jinja filter that safely encodes a Python string as a JSON string literal (handling quote-escaping, etc.) so the embedded JSON-LD block stays valid even if a product name ever contains a quote character.

Now wire it into `merchant/app.py`:
```python
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from merchant.catalog.yaml_adapter import YAMLCatalogAdapter
from merchant.config import settings
from merchant.db import init_db
from merchant.models import AgentCommerceDescriptor, MerchantDescriptor, AuthDescriptor, PolicyDescriptor

app = FastAPI(title="OpenStore Merchant Server")
templates = Jinja2Templates(directory="merchant/storefront")
catalog = YAMLCatalogAdapter(settings.merchant_config_path)

@app.on_event("startup")
def on_startup():
    init_db()

@app.get("/")
def storefront(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "merchant_name": "Gelateria Roma",
            "products": catalog.list_all(),
        },
    )

@app.get("/.well-known/agent-commerce.json")
def agent_commerce():
    return AgentCommerceDescriptor(
        merchant=MerchantDescriptor(name="Gelateria Roma", id="gelateria-roma"),
        storefront="http://localhost:8000/",
        catalog_endpoint="http://localhost:8000/agent/catalog",
        mcp_endpoint="http://localhost:8000/agent/mcp",
        a2a_agent_card="http://localhost:8001/.well-known/agent-card.json",
        auth=AuthDescriptor(
            authorization_server="http://localhost:8000/.well-known/oauth-authorization-server",
            scopes_supported=["catalog:read", "cart:write", "checkout:initiate", "checkout:confirm"],
        ),
        policy=PolicyDescriptor(
            currency="INR",
            max_unconfirmed_spend_minor=0,
            requires_human_approval=True,
            default_per_tx_cap_minor=50000,
        ),
    )
```

`@app.on_event("startup")` registers a function FastAPI runs once, when the ASGI server actually starts (as opposed to just importing the module) — the right place to call `init_db()`, since you want the WAL pragma and table creation to happen exactly once per process start, not on every import.

Returning a Pydantic model instance (`AgentCommerceDescriptor(...)`) directly from a route is something FastAPI handles natively — it serializes the model to JSON automatically, using the field names and types you declared, which is the payoff of having frozen this as a Pydantic model in Step 3 instead of hand-building a dict.

### Step 7 — `trace.py`, the observability system, built first

**What**: a single function, `emit(...)`, that posts a formatted Discord embed to one of your four webhook channels, tagged with a `trace_id` so you can follow one request's story across all four channels later.
**Tool**: `httpx`, Discord webhook JSON embed format.
**Why**: the plan is explicit that this must exist *before* any business logic, because retrofitting logging after the fact means you'll have gaps exactly where debugging would have mattered most. Build the muscle now: every tool call, every state transition, every rejection emits a trace, starting today.

A `trace_id` is just a unique identifier (a UUID) that you generate once per logical operation (e.g., once per checkout attempt) and pass through every function call involved in that operation, attaching it to every trace emission. This is the same idea as a "correlation ID" or "request ID" in production systems — its entire purpose is letting you filter/search "everything that happened during this one operation," across multiple channels or even multiple processes, after the fact.

`merchant/trace.py`:
```python
import httpx
from datetime import datetime
from merchant.config import settings

CHANNEL_WEBHOOKS = {
    "buyer-agent": settings.discord_webhook_buyer_agent,
    "merchant-agent": settings.discord_webhook_merchant_agent,
    "merchant-server": settings.discord_webhook_merchant_server,
    "audit-trail": settings.discord_webhook_audit_trail,
}

LEVEL_COLORS = {
    "info": 0x3498DB,      # blue
    "gate": 0xF1C40F,      # amber
    "blocked": 0xE74C3C,   # red
    "executed": 0x2ECC71,  # green
}

def emit(channel: str, title: str, fields: dict, trace_id: str, level: str = "info"):
    webhook_url = CHANNEL_WEBHOOKS[channel]
    embed = {
        "title": title,
        "color": LEVEL_COLORS.get(level, LEVEL_COLORS["info"]),
        "fields": [
            {"name": k, "value": str(v), "inline": True}
            for k, v in fields.items()
        ],
        "footer": {"text": f"trace_id={trace_id}"},
        "timestamp": datetime.utcnow().isoformat(),
    }
    try:
        httpx.post(webhook_url, json={"embeds": [embed]}, timeout=5.0)
    except httpx.HTTPError:
        # Tracing must never crash the request it's tracing.
        pass
```

The `try/except` around the actual network call matters: if Discord is briefly unreachable, or a webhook URL is wrong, that must **never** take down a real checkout — a trace call failing silently is an acceptable loss; a trace call failing *loudly enough to break the operation it's tracing* is not. This is a small but real design decision, and it's the kind of thing that's easy to get backwards if you don't think about it up front.

Wire a startup trace to prove the pipeline works end-to-end:
```python
import uuid

@app.on_event("startup")
def on_startup():
    init_db()
    emit(
        channel="merchant-server",
        title="Merchant server started",
        fields={"merchant": "gelateria-roma"},
        trace_id=str(uuid.uuid4()),
        level="info",
    )
```

---

## Running it

```bash
uv run uvicorn merchant.app:app --reload --port 8000
```

Visit `http://localhost:8000/` — you should see the storefront list. View source and confirm the JSON-LD `<script>` blocks are present and well-formed. Visit `http://localhost:8000/.well-known/agent-commerce.json` and confirm it matches the shape of your committed fixture.

## Exit check (from the plan)

> ✅ Exit: storefront renders, well-known validates, a test trace lands in `#merchant-server`.

Concretely, verify all three:

1. **Storefront renders** — load `http://localhost:8000/` in a browser, see 10 products with names, descriptions, prices.
2. **Well-known validates** — two checks, not one: (a) `curl http://localhost:8000/.well-known/agent-commerce.json | python3 -m json.tool` parses without error and matches your `tests/fixtures/agent_commerce.json` shape; (b) paste your storefront URL into [Google's Rich Results Test](https://search.google.com/test/rich-results) and confirm it detects valid `Product`/`Offer` markup with no errors.
3. **A test trace lands in `#merchant-server`** — start the server, and confirm a real Discord message appears in that channel with the title "Merchant server started," a blue left border, and a `trace_id` in the footer. If nothing appears, see below.

## What could go wrong

- **Nothing shows up in Discord, no error in your terminal**: your webhook URL is likely wrong or the channel/webhook was deleted. Test it in isolation first: `curl -X POST -H "Content-Type: application/json" -d '{"content":"test"}' <your-webhook-url>` — if this doesn't post, the problem is the URL/webhook itself, not your Python code.
- **`ValidationError` on `Settings()` construction at import time**: a required field in `.env` is missing or misspelled. Pydantic will tell you exactly which field — read the error, don't guess; a common mistake is a typo between the `.env` key and the `Settings` field name (they're matched case-insensitively but must otherwise match exactly).
- **"database is locked" errors**: you forgot the WAL pragma, or you're running two server processes against the same `.db` file simultaneously (e.g., an old `uvicorn --reload` process didn't actually die). Kill stray processes (`pkill -f uvicorn` on macOS/Linux) and confirm `init_db()` ran.
- **JSON-LD validates in your eyes but not Google's tool**: the most common mistake is forgetting `|tojson` on a string field, which can leave an unescaped quote or an un-quoted JSON value depending on what's in the string — always use `|tojson` for anything going inside the JSON-LD block, never manual string interpolation.
- **`Product(**p)` throws a `ValidationError` while loading YAML**: check for a typo in a field name in `gelateria.yaml`, or a YAML indentation error that nested a field under the wrong key. Pydantic's error message names the exact field it couldn't find or validate.

---

Tomorrow: the OAuth 2.1 authorization server. Today's contracts (`AgentCommerceDescriptor`'s `auth` field, in particular) are what Day 2 implements against.
