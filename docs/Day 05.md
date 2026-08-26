# Day 5 — Buyer Agent + Discord

**Date: Sunday, August 30, 2026.**

From here on, the depth tapers the way `00-START-HERE.md` promised: FastAPI routes, SQLModel queries, trace emission, and Pydantic contracts are all patterns you've now built by hand multiple times, so they show up from here as **signatures only** — name, inputs, outputs, one line of behavior. Genuinely new concepts (today: LangGraph, `interrupt()`, checkpointing) still get the full treatment.

## What you'll have by tonight

A fully separate, isolated `buyer_agent/` package — one that imports _nothing_ from `merchant/` or `merchant_agent/` — that discovers your merchant purely from its `.well-known` document, does the full OAuth PKCE dance itself, and runs a LangGraph-driven conversation in a real Discord channel: search, cart, checkout-initiate, pause for human approval, resume, confirm. Driven entirely from chat.

---

## Concepts

### 1. Why the buyer agent is architecturally isolated, and how that's enforced, not just claimed

The plan's repo layout comment is blunt: `buyer_agent/ — isolated: imports NOTHING from merchant/ or merchant_agent/`. This matters because the buyer agent represents "any third-party AI agent that might want to shop at your store" — a real one would be a completely separate codebase, written by someone who's never seen your merchant server's internals, built entirely against your `.well-known` document and MCP tool schemas. If your buyer agent quietly imported a helper from `merchant/`, your demo's "generic, standardized" claim would be fiction — you'd have built a system that only works because one privileged client cheats.

The plan enforces this as a **test**, not a doc comment: `tests/test_isolation.py` walks the `buyer_agent/` package's Python **AST** (Abstract Syntax Tree — the parsed structure of your code, before it's compiled/run) looking for any `import merchant` or `import merchant_agent` statement, and fails the test suite if it finds one. This converts an architectural intention into something CI-checkable — you'll write this in full on Day 7, but keep the discipline in mind starting now, before you're tempted to take a shortcut.

### 2. LangGraph: agents as explicit state machines, not implicit loops

You may have seen "agent" frameworks built around an implicit loop: call the LLM, see if it wants to call a tool, call the tool, feed the result back, repeat until the LLM produces a final answer. This works, but it's opaque — you can't easily say "pause here and wait for a human," or "this step must always happen regardless of what the LLM decides," because the control flow lives inside a black-box loop.

**LangGraph** makes the control flow an explicit graph: you define **nodes** (a node is a function — often, but not always, one that calls an LLM) and **edges** (which node runs next, either fixed or conditional on the current **state**). State is a shared, typed object (often a `TypedDict` or Pydantic model) that flows through the graph, accumulated and mutated by each node.

```python
from langgraph.graph import StateGraph, END
from typing import TypedDict

class ConversationState(TypedDict):
    conversation_id: str
    buyer_id: str
    cart_id: int | None
    cart_version: int | None
    pending_checkout_id: str | None
    approval_status: str | None
    last_user_message: str

graph = StateGraph(ConversationState)
graph.add_node("classify", classify_node)
graph.add_node("search", search_node)
graph.add_edge("classify", "search")
```

Why does this matter for OpenStore specifically? Because the plan's single most important sentence about the buyer agent is: **"the graph has no node that decides to charge."** With an explicit graph, you can _prove_ this by inspection — walk the node list, and there is no node whose job is "decide to spend money." The terminal node calls exactly one deterministic function, `checkout_confirm(mandate, idempotency_key)`, and every security check from Day 4 re-runs server-side inside that call regardless of what any node "decided." The graph structure is what makes "the reasoning layer cannot move money" a checkable architectural fact rather than a hopeful description of what the LLM was told to do.

### 3. `interrupt()` and checkpointing — pausing a graph mid-execution, safely

Somewhere in the middle of this conversation, the graph needs to **stop and wait** — potentially for minutes, while a human checks Discord, clicks Approve, and enters an OTP. A normal Python function call can't "pause" like this across an indefinite real-world wait without either blocking a thread the whole time (wasteful, and fragile if the process restarts) or requiring you to hand-roll your own resumable-state bookkeeping.

LangGraph's `interrupt()` function, called from inside a node, does exactly this: it suspends the graph's execution at that exact point, returning control to whatever's driving the graph (your Discord bot's message handler), and the graph's _entire state_ is durably saved via a **checkpointer**. Later — possibly after the whole process restarts — you resume the graph, and it picks up exactly where it left off, as if the pause never happened from the graph's perspective.

```python
from langgraph.types import interrupt

def request_approval_node(state: ConversationState) -> ConversationState:
    # ... call checkout_initiate via MCP ...
    human_decision = interrupt({"reason": "awaiting_approval", "checkout_id": checkout_id})
    # execution genuinely pauses here until resumed
    return {**state, "approval_status": human_decision}
```

`SqliteSaver` is LangGraph's checkpointer backed by SQLite (fitting neatly with the rest of this project's storage choices) — it persists the graph's state to disk at each step, which is what makes resume-after-restart actually work rather than just resume-within-the-same-process-lifetime. The plan calls out testing this specifically ("test resume-after-restart on Day 5") as Risk R6 — it's exactly the kind of thing that looks fine in a quick demo but silently breaks if your process happens to restart (a code reload, a crash, a redeploy) while a checkout is mid-approval.

### 4. MCP tools as LangChain tools

Day 3 built your merchant's MCP server. Today's buyer agent needs to actually _call_ those tools from inside a LangGraph node. `langchain-mcp-adapters` is a small library that connects to any MCP server and wraps its tools as LangChain-compatible tool objects — meaning your LangGraph nodes (or an LLM given a tool-calling prompt) can call `search_products(...)` the same way they'd call any other LangChain tool, without you writing per-tool glue code. This is the buyer-side mirror of Day 3's server-side MCP work — the same protocol, consumed instead of served.

---

## Build

### Step 1 — `discover.py`: fetch `.well-known`, then AS metadata

**Signature:**

```python
def discover_merchant(base_url: str) -> AgentCommerceDescriptor:
    """GET {base_url}/.well-known/agent-commerce.json, parse into the
    AgentCommerceDescriptor model. This is the ONLY thing buyer_agent
    knows about the merchant before this call returns."""

def discover_auth_server(descriptor: AgentCommerceDescriptor) -> dict:
    """GET descriptor.auth.authorization_server, return the parsed
    RFC 8414 metadata dict (endpoints, supported grant types)."""
```

Both fetch real HTTP endpoints your merchant server already serves (from Days 1–2) and parse the response into the same Pydantic model your merchant server used to _produce_ it — reusing the model definition (copy the `AgentCommerceDescriptor` class into `buyer_agent/`, don't import it from `merchant/`, per the isolation rule) means a shape mismatch is caught immediately as a validation error, not a silent misparse.

### Step 2 — `oauth_client.py`: PKCE, loopback listener, token cache

**Signatures:**

```python
def generate_pkce_pair() -> tuple[str, str]:
    """Returns (code_verifier, code_challenge). Same math as Day 2's Concepts
    section — SHA-256 the verifier, base64url-encode, strip padding."""

def start_loopback_listener(port: int = 8765) -> "LoopbackServer":
    """Starts a tiny local HTTP server on 127.0.0.1:port with one route,
    /callback, that captures the ?code= and ?state= query params and
    stores them for retrieval, then can be told to shut down."""

def open_authorize_url(auth_endpoint: str, client_id: str, scopes: list[str],
                        redirect_uri: str, code_challenge: str, state: str) -> None:
    """Builds the full /oauth/authorize URL and opens it in the system's
    default browser via Python's webbrowser module."""

def exchange_code_for_token(token_endpoint: str, code: str, code_verifier: str,
                             redirect_uri: str, client_id: str) -> dict:
    """POSTs the authorization_code grant, same shape as Day 2's curl test.
    Returns the full token response (access_token, refresh_token, expires_in)."""

def load_or_refresh_token(cache_path: str, token_endpoint: str, client_id: str) -> str:
    """Reads a cached token from disk; if expired, uses the stored
    refresh_token to get a new access_token via the refresh_token grant
    (Day 2's token endpoint already supports this grant type). Returns a
    valid access_token, doing the full PKCE dance from scratch only if no
    usable cache/refresh exists."""
```

This is the exact PKCE flow you drove by hand with `curl` on Day 2 — now automated end-to-end from the client's side. `webbrowser.open(url)` (Python standard library) opens the consent URL in the user's actual browser; the loopback listener catches the redirect Day 2's `/oauth/consent` handler sends once the human clicks Approve there.

**On 401**: any MCP tool call that comes back `401 Unauthorized` should trigger `load_or_refresh_token` again before retrying once — access tokens are short-lived (15 minutes, from Day 2) by design, so refreshing is a routine, expected occurrence, not an error path.

### Step 3 — `graph.py`: the LangGraph state machine

**What**: the actual graph — nodes and edges implementing the flow described in Concepts. **Signatures for each node** (a node in LangGraph is just a function from state to a partial state update):

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

def request_approval_node(state: ConversationState) -> dict:
    """Calls checkout_initiate, then calls interrupt() to pause the graph
    until a human resumes it. Returns {'approval_status': <result after resume>}."""

def confirm_node(state: ConversationState) -> dict:
    """The ONLY node that spends money. Calls checkout_confirm(mandate,
    idempotency_key) — a single deterministic call. Returns {'order_result': dict}."""

def explain_node(state: ConversationState) -> dict:
    """Formats the final result (or rejection reason) into a human-readable
    message for the Discord reply. Returns {'reply_text': str}."""
```

Wire them with `graph.add_node(name, fn)` and `graph.add_edge(...)` following the plan's stated order: `classify → search → consult merchant agent (stub today) → summarize cart → request approval → interrupt() → confirm → explain`. Compile with a `SqliteSaver` checkpointer:

```python
from langgraph.checkpoint.sqlite import SqliteSaver

checkpointer = SqliteSaver.from_conn_string("buyer_agent_state.db")
compiled_graph = graph.compile(checkpointer=checkpointer)
```

**Test resume-after-restart today**, per Risk R6: start a conversation, let it hit the `interrupt()` in `request_approval_node`, kill the buyer agent process entirely, restart it, and confirm that resuming the same `conversation_id` (thread/checkpoint ID) picks the graph back up at the interrupt point rather than starting over or erroring. If this doesn't work, fix it now — Day 9's live demo cannot afford a mid-demo process hiccup silently losing checkout state.

### Step 4 — `bot.py`: the OpenStore Buyer Discord bot

**Signatures:**

```python
async def on_message_handler(message: discord.Message) -> None:
    """Registered against the #buyer-agent channel. Loads/creates the
    conversation's checkpoint (conversation_id keyed by channel+thread or
    user id), invokes compiled_graph.invoke(...) or .stream(...) with the
    new message, and sends state['reply_text'] back to the channel. If the
    graph is paused on interrupt(), instead sends: '⏸️ Waiting for approval
    — check your DMs.'"""

def resume_after_approval(conversation_id: str, approval_result: dict) -> None:
    """Called once the merchant server's OTP-verify flow completes (you'll
    need a small bridge — e.g. the buyer agent polls checkout status, or
    the notifier posts to a small local endpoint the buyer agent exposes —
    pick whichever is simpler for your setup) to resume the graph past its
    interrupt() with the approval outcome."""
```

The bridge between "human approved in a _different_ Discord bot's DM" and "resume _this_ bot's paused graph" is a genuine design decision you'll need to make concretely rather than copy verbatim — the plan doesn't over-specify it, and it's a reasonable place to exercise judgment: a small polling loop (buyer agent checks `GET /agent/mcp` → `get_order`/checkout status every few seconds while paused) is simple and robust; a direct callback from the notifier is faster but couples the two bots together more tightly. Either is defensible; pick one and be consistent.

---

## Exit check (from the plan)

> ✅ Exit: full happy path driven entirely from Discord chat. **Record this immediately** — it's your fallback demo asset.

Concretely: from a fresh Discord message in `#buyer-agent` (something like "I want a pistachio gelato"), the conversation should flow through search → cart → checkout-initiate → the "waiting for approval" message → (you approve via DM on the _other_ bot) → resume → confirm → a final reply confirming the order, with the actual payment completed on Razorpay's test dashboard by the end.

**Record a screen capture of this succeeding, today**, exactly as the plan says. Don't wait until Day 9 to have a working recording — if something breaks between now and the live demo, this recording is your fallback, and a fallback recorded under time pressure on Day 9 is a worse fallback than one recorded calmly today while everything is fresh.

## What could go wrong

- **`interrupt()` throws instead of pausing**: confirm the graph was compiled `with checkpointer=checkpointer` — `interrupt()` requires a checkpointer to be configured; without one, LangGraph has nowhere to persist the paused state and will error instead of suspending.
- **Resume "works" but state is stale/wrong**: double check you're resuming with the _same_ `conversation_id`/thread ID used originally — LangGraph checkpoints are keyed by a thread identifier you control; reusing the wrong key silently starts a fresh, empty conversation instead of resuming the one you meant.
- **PKCE flow works via curl (Day 2) but fails from the buyer agent**: the most common gap is a `redirect_uri` mismatch — the URI registered at `/oauth/register` must match, character-for-character, the one sent in both the `/oauth/authorize` request and the `/oauth/token` exchange. `http://127.0.0.1:8765/callback` and `http://127.0.0.1:8765/callback/` are different strings to a strict comparison.
- **Buyer agent's isolation test (mental check today, real test on Day 7)**: if you find yourself about to write `from merchant.models import Product` inside `buyer_agent/`, stop — copy the shape you need instead. It's more typing, and it's the entire point of today's architecture.

---

Tomorrow: the merchant reasoning agent, over A2A, with its own credential discipline — and today's `consult_merchant_agent_node` stub gets wired to something real.