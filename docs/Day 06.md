# Day 6 — Merchant Reasoning Agent (A2A)

**Date: Monday, August 31, 2026.**

Today you build the process the plan is most paranoid about — the merchant _reasoning_ agent. It's the piece with the LLM in it on the merchant's side, and per the architecture diagram from Day 1, it holds **no signing key and no Razorpay write credentials, full stop.** Today is where you prove that isn't just a comment in a README.

## What you'll have by tonight

A separate process, `merchant_agent/`, exposing an A2A agent card and three skills — cross-sell suggestions, campaign draft, and read-only finance Q&A — reachable by the buyer agent's `consult_merchant_agent_node` (stubbed on Day 5, wired for real today), using a read-only database session throughout.

---

## Concepts

### 1. What A2A is, and how it's different from MCP

Day 3 covered MCP: a protocol for an **agent to call tools** exposed by a server. **A2A (Agent-to-Agent protocol)**, also from the same open standardization effort, addresses a different shape of interaction: **agent talks to agent**, where the other side isn't a fixed set of tools but another autonomous reasoning system — one that might itself decide how to handle a request, potentially taking multiple steps, potentially over an extended time.

Concretely: your buyer agent doesn't need to know _how_ the merchant reasoning agent decides on a cross-sell suggestion — it just sends a natural-language-ish task ("given this cart, suggest a complementary product") and gets back a result, the same way you might ask a colleague something without needing to know their exact thought process. This is the right protocol for merchant-to-merchant-agent communication specifically because the _reasoning_ is genuinely on the other side — unlike an MCP tool call, which is closer to a well-defined function call with a fixed contract.

An A2A server exposes an **agent card** — a discovery document (similar in spirit to Day 1's `.well-known/agent-commerce.json`, but standardized for agent capabilities specifically) at `.well-known/agent-card.json`, listing the agent's name, description, and its **skills** — named capabilities, each with its own description an LLM-driven caller can read to decide when to invoke it.

### 2. JSON-RPC and SSE — the transport A2A rides on

**JSON-RPC** is a lightweight remote-procedure-call protocol: a request is a JSON object naming a `method` and `params`; a response is a JSON object with either a `result` or an `error`. It's older and simpler than REST-style APIs (no resource/URL modeling, no HTTP verb semantics — just "call this method with these params, get this result back"), and A2A adopted it as its base message format.

**SSE (Server-Sent Events)** is an HTTP mechanism for a server to push a _stream_ of events to a client over a single long-lived connection, more lightweight than a WebSocket when you only need one-directional server→client streaming. A2A uses SSE so a long-running agent task (the reasoning agent thinking through a multi-step response) can stream partial updates/status back to the caller, rather than the caller blocking silently until the entire task finishes.

### 3. The A2A task lifecycle

An A2A interaction isn't always a single request/response — a **task** has a lifecycle: `submitted → working → (optionally: input-required) → completed` (or `failed`, `canceled`). This matters here because a real reasoning agent invocation (an LLM call, possibly a multi-step one) isn't instantaneous — the caller can poll or stream task status rather than holding a synchronous HTTP connection open indefinitely, and the protocol standardizes what "still working" vs. "done" vs. "needs more info from you" look like across any A2A-compliant client/server pair, not just this one.

For OpenStore's scope, your three skills are each simple enough to complete synchronously within a normal request/response — you don't need to build out elaborate `input-required` handling — but structure your task state transitions correctly (`submitted` → `working` → `completed`/`failed`) so the plumbing is honest about what's happening, even if every task in this demo completes quickly.

### 4. Read-only credential separation, concretely

The plan's word for this is blunt: _"NO Razorpay write creds, NO signing key."_ Concretely, this means:

- The merchant reasoning agent's database session is opened with a genuinely different, lower-privilege path than the merchant execution server's — the cleanest way to enforce this in SQLite is a **separate `Session`/connection configured to never call `session.add()`/`session.commit()` on money-relevant tables**, combined with simply _never importing_ `merchant.mandate` (the Ed25519 signing module) or `merchant.razorpay_client` (the SDK wrapper with real API keys) anywhere in `merchant_agent/`'s codebase. SQLite itself doesn't have per-connection user-level permissions the way Postgres does, so the enforcement here is architectural (which modules get imported, which credentials get loaded into this process's environment at all) rather than a database-level `GRANT`/`REVOKE` — which is exactly consistent with the plan's own framing of this as a **process and credential separation**, not a database permissions feature.
- Concretely: `merchant_agent/.env` (a separate env file from `merchant/.env`) should contain **no** `RAZORPAY_KEY_SECRET` and **no** path to `merchant_signing_key.pem` at all. If the credential simply isn't present in this process's environment, it structurally cannot be used by this process, accidentally or otherwise — the strongest version of this guarantee, stronger than "the code happens not to call it today."

---

## Build

### Step 1 — `merchant_agent/agent_card.py`: A2A discovery document

**Signature:**

```python
def get_agent_card() -> dict:
    """Returns the A2A AgentCard: name, description, url (this agent's own
    A2A endpoint), and a `skills` list — one entry per skill below, each with
    an id, name, description (LLM-readable), and input/output schema hints."""
```

Serve it at `GET /.well-known/agent-card.json` on this agent's own FastAPI app (yes, a second, separate FastAPI app — `merchant_agent/` is its own process, its own `uvicorn` invocation, its own port, e.g. `:8001`, matching the `a2a_agent_card` URL you already put in Day 1's `agent-commerce.json`).

### Step 2 — Read-only session helper

**Signature:**

```python
def get_readonly_session() -> Session:
    """Opens a SQLModel Session against the SAME database file as the
    merchant server (read access to Product/Cart/Order/AuditLogEntry data is
    the whole point of the finance-Q&A skill), but this module — and nothing
    else in merchant_agent/ — ever calls .add() or .commit(). Enforce this by
    convention + code review discipline here, since SQLite has no native
    per-connection write permission; the real guarantee is architectural:
    this process's .env has no signing key or Razorpay write secret at all."""
```

### Step 3 — Skill 1: `cross_sell`

**What**: given a cart's contents, suggest a complementary product using the `related_skus`/`tags` metadata you seeded into `gelateria.yaml` back on Day 1 Step 5. **Signature:**

```python
def cross_sell_skill(cart_items: list[dict]) -> dict:
    """Looks up related_skus for each item in cart_items (read-only catalog
    lookup, same YAMLCatalogAdapter class as Day 1/3, loaded fresh in this
    process), optionally asks an LLM to phrase a natural suggestion, and
    returns {'suggested_sku': str, 'reason': str}. Never touches Cart or
    Checkout tables — pure suggestion, no side effect."""
```

This is a good first skill precisely because it's read-only in the most literal sense: it reads the catalog (which is a config file, not even the database) and returns a suggestion — the buyer agent's node decides whether to surface it to the human, and nothing about accepting or rejecting the suggestion touches money.

### Step 4 — Skill 2: `campaign_draft`

**What**: given an occasion/tag (e.g., `occasion:birthday`, from the tags you seeded on Day 1), draft a short promotional message a merchant could review before sending — text generation, not an action. **Signature:**

```python
def campaign_draft_skill(occasion_tag: str) -> dict:
    """Filters the catalog for products matching occasion_tag, asks an LLM
    to draft a short promotional blurb referencing them, returns
    {'draft_text': str, 'referenced_skus': list[str]}. Purely generative —
    produces text for a human to review/send elsewhere, initiates nothing."""
```

**Cut-line reminder** (plan §8, cut-line 1): if by end of today the A2A agent process isn't reliably running end-to-end, drop this skill first and keep `cross_sell` (used live in the Day 5 buyer flow) and `finance_qa` (a strong, distinct capability demo). `campaign_draft` is the most dispensable of the three because nothing else in the system depends on it.

### Step 5 — Skill 3: `finance_qa`

**What**: answer simple, read-only questions about the merchant's own order history — "how many orders today," "total revenue this week" — by querying the database directly, never guessing or hallucinating numbers. **Signature:**

```python
def finance_qa_skill(question: str) -> dict:
    """Classifies the question into one of a small fixed set of supported
    query shapes (order count in a date range, revenue sum in a date range —
    do NOT attempt open-ended free-form SQL generation from the LLM, that's
    a real injection/correctness risk for no benefit here), runs the
    corresponding read-only SQLModel query via get_readonly_session(), and
    returns {'answer_text': str, 'figures': dict} with the LLM only used to
    phrase the answer in natural language, never to decide what data to
    fetch or compute the numbers itself."""
```

The explicit note in that signature matters: it's tempting to let an LLM translate a free-form question directly into a SQL query (a common "text-to-SQL" pattern), but for a finance-facing skill in a security-conscious demo project, that's the wrong trade — you'd be reintroducing exactly the "never trust agent-generated queries against money data" problem Day 3/4 spent so much effort structurally avoiding, just one layer further up. A small fixed set of pre-written, parameterized queries, selected by (simple, low-stakes) classification, keeps the actual data access deterministic and auditable.

### Step 6 — A2A server wiring

**Signature:**

```python
def build_a2a_app() -> "FastAPI":
    """Constructs the A2A-compliant FastAPI app: serves the agent card,
    accepts JSON-RPC task submissions at the agent's endpoint, dispatches
    to the matching skill function by skill id, and returns/streams the
    task result per the A2A task lifecycle (submitted -> working ->
    completed/failed)."""
```

Use whichever official/community A2A Python SDK is current (check for one alongside `mcp` in your package search — the ecosystem here moves quickly, so verify against current docs rather than assuming a specific package name) — its job is handling the JSON-RPC framing and task lifecycle bookkeeping so your skill functions above can stay plain Python functions with no protocol-specific code inside them, the same separation of concerns Day 3's FastMCP gave you for tools.

### Step 7 — Wire `consult_merchant_agent_node` for real

Back in `buyer_agent/graph.py` from Day 5, replace the stub:

**Signature:**

```python
def consult_merchant_agent_node(state: ConversationState) -> dict:
    """Sends a JSON-RPC task to the merchant agent's A2A endpoint (URL from
    the a2a_agent_card field discovered on Day 5) requesting the cross_sell
    skill with the current cart's items, awaits task completion, and
    returns {'cross_sell_suggestion': dict | None} for summarize_cart_node
    to optionally surface to the human."""
```

---

## Exit check (from the plan)

> ✅ Exit: buyer agent hits merchant agent over A2A live, gets a cross-sell suggestion mid-conversation.

Concretely: run through Day 5's happy path again from Discord, but this time confirm that somewhere in the flow (after items are in the cart, before checkout), a real message appears — sourced from an actual A2A call to a separately-running `merchant_agent/` process, not a hardcoded string — surfacing a related-product suggestion. Check both bot logs/traces to confirm the round trip actually crossed process boundaries: you want to see the request land in the merchant agent's own trace/log output, not just infer it happened because _some_ suggestion appeared.

## What could go wrong

- **A2A call hangs indefinitely**: confirm the merchant agent process is actually running on the port your `agent-commerce.json`'s `a2a_agent_card` URL points to — a very easy Day 5/6 mismatch is forgetting to update that URL once you actually pick a real port for `merchant_agent/`.
- **Cross-sell suggestions look plausible but reference SKUs that don't exist**: this means the skill is letting the LLM invent a SKU rather than selecting strictly from `related_skus` values already present in the catalog data you fetched — constrain the LLM's output to only choose among a supplied candidate list, don't let it free-generate a SKU string.
- **`finance_qa` gives a wrong number that "sounds" right**: if you did end up letting the LLM anywhere near computing the figure instead of your own SQL query providing it verbatim, this is the bug — re-read the Step 5 signature note and fix the boundary between "LLM phrases" and "code computes."
- **Tempted to just import `merchant.db.engine` directly in `merchant_agent/` "since it's the same database anyway"**: resist this — even though it's technically the same SQLite file, importing from `merchant/` at all breaks the process/module separation the whole security story depends on being visibly true, not just true by coincidence of file paths. Open your own `create_engine(...)` in `merchant_agent/` pointed at the same `database_url` value (loaded from _this_ process's own `.env`), rather than importing the merchant server's engine object.

---

Tomorrow: you stop building forward and start trying to break everything you've built.