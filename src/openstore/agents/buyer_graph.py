# OpenStore agents — BuyerGraph (S12/S13): a real tool-calling LLM loop for
# buyer planning, not a fixed pipeline. The LLM decides whether to search the
# catalog (possibly more than once, with different queries), ask the buyer a
# clarifying question / offer alternatives, or answer with a final selection.
# Python only ever executes what the LLM proposes after validating it against
# real data (R0.9): search is read-only catalog lookup, an "ask" is free text
# shown to the buyer (never touches cart/money state), and an "answer" must
# name only skus that actually came back from a search this turn — price/tags
# are always copied from the real catalog item, never the LLM's own JSON.
#
# Both the per-turn search-call cap and (in buyer_agent.py) the cross-turn
# conversation-length cap live in Python, outside the LLM's reach (R0.5:
# bounded, never a silent/unbounded loop).
#
# NO payment/PSP/signing material here (R0.10).

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph

from openstore.agents.llm import llm_chat
from openstore.config import Settings, merchant_id
from openstore.notifier import DiscordNotifier

logger = logging.getLogger("openstore.buyer_graph")

MAX_TOOL_CALLS_PER_TURN = 3
# S17: a bound on how many keyword terms one "search" action may pack in —
# independent of MAX_TOOL_CALLS_PER_TURN (which bounds how many SEARCH
# ACTIONS happen this turn). Without this, one action could smuggle in
# unlimited catalog lookups; this keeps the "one action = one bounded unit
# of work" invariant while still letting one action cover multiple items.
MAX_QUERIES_PER_SEARCH = 5
_NO_MATCH_AFTER_SEARCH_QUESTION = (
    "I couldn't find a great match after a few tries — can you tell me more "
    "about what you're looking for?"
)

_ACTIONS = frozenset(
    {"search", "ask", "answer", "show_menu", "show_cart", "check_order_status", "cancel_order"}
)


class BuyerPlanError(Exception):
    """Raised when the LLM's response violates the closed output contract for
    a buyer-planning step (invalid JSON, unrecognized action, malformed
    selection, a sku that never appeared in a search result, etc). Distinct
    from llm.LLMError, which means the provider/transport itself failed."""


class BuyerPlanState(TypedDict):
    goal: str
    policy_id: str
    trace_id: str
    messages: list[dict[str, str]]
    all_search_results: dict[str, dict[str, Any]]
    tool_calls_used: int
    cart: list[dict[str, Any]]
    pending_question: NotRequired[str]
    # S15: fired with the query right before each search runs, so a caller
    # (BuyerBot) can show "Looking for X…" in chat. Carried in per-invocation
    # state (not a BuyerGraph instance attribute) because one BuyerGraph is
    # shared across concurrent buyers — an instance attribute would race.
    on_search: NotRequired[Callable[[str], Awaitable[None]] | None]
    # S18: fired by the show_menu/show_cart actions (free-text "what's on
    # the menu"/"what's in my cart" — no bang command needed). Same
    # per-invocation-state reasoning as on_search: these render a Discord
    # embed as a side effect, but the actual catalog/cart fetch and
    # rendering live in buyer_agent.py (Discord-specific), not here.
    on_menu_ready: NotRequired[Callable[[str | None], Awaitable[None]] | None]
    on_cart_shown: NotRequired[Callable[[list[dict[str, Any]]], Awaitable[None]] | None]
    # S14: fired by check_order_status/cancel_order (free-text "did my order
    # go through"/"cancel my order"). Same reasoning as on_menu_ready — the
    # actual list_orders/cancel_order MCP calls and the buyer's identity
    # (chat_platform/chat_user_id, which this state doesn't carry at all)
    # live entirely in buyer_agent.py; these nodes only validate the LLM's
    # draft and hand off.
    on_order_status: NotRequired[Callable[[], Awaitable[None]] | None]
    on_cancel_requested: NotRequired[Callable[[], Awaitable[None]] | None]


_SYSTEM_PROMPT = (
    "You are a shopping assistant helping a buyer fulfil a stated goal from a "
    "merchant's catalog. You have exactly seven actions available, and must "
    "respond with JSON only, matching one of these seven shapes:\n"
    '1. {"action": "search", "query": "<short keyword, e.g. \'vanilla\' or '
    '\'gelato\'>", "tags": ["<optional tag filter>"]} — looks up the catalog. '
    '"query" may ALSO be a list of keywords, e.g. "query": ["vanilla", '
    '"pistachio", "waffle cone"] — use this whenever the buyer names more '
    "than one distinct item in the same message, so one search action covers "
    "all of them instead of using up a separate search action per item (you "
    "only get a few search actions per turn). If your first search doesn't "
    "find an exact match, search again with a BROADER term (e.g. the general "
    "category, not the specific flavor) before giving up — you need to "
    "actually see what's available before you can offer it. A search result "
    'item MAY include "discount_bps" and "campaign_title" — a REAL, already-'
    "verified live discount on that exact item (never something you add "
    "yourself). If the buyer asks about offers, deals, or discounts, or if "
    "an item you're about to mention has one, say so and name the "
    "campaign_title and the percentage (discount_bps / 100). Items with no "
    "such field have no active offer — say that plainly, never invent one.\n"
    '2. {"action": "ask", "message": "<plain-text question or offer for the '
    'buyer>"} — use this when nothing you found matches exactly. You may '
    "name a SPECIFIC product in this message ONLY if it literally appeared "
    "in a search result you already saw this conversation — never invent, "
    "guess, or assume a flavor/product exists just because it sounds "
    "plausible. If you haven't searched broadly enough to know what real "
    "alternatives exist, do that (action: search) before asking. If you "
    "genuinely don't know what else is available, ask a general question "
    "instead of naming unconfirmed products. This ends your turn; the "
    "buyer's reply continues the conversation.\n"
    '3. {"action": "answer", "selections": [{"sku": "<exact sku from a '
    'search result>", "merchant_id": "<merchant_id from that SAME search '
    'result>", "qty": N}]} — propose the order (selections may list more '
    "than one product — a buyer can ask for several different items in one "
    "order). Every sku MUST come from a search result you already saw this "
    "conversation, and merchant_id MUST be copied from that same result "
    "(different stores can sell the same sku) — never invent a sku, "
    "merchant_id, price, or tag. This does NOT place the order yet — the "
    "buyer is shown the cart and must confirm.\n"
    "Prefer searching broadly and offering real alternatives over silently "
    "giving up — but never over naming something you haven't confirmed "
    "exists.\n"
    "If the transcript already contains a tool_result with "
    '"tool_result": "cart_ready" (a cart you already proposed) and the '
    "buyer's latest message is anything other than a plain confirmation, "
    "treat it as a request to change the order — search again first if it "
    "names something you haven't seen — then respond with a NEW answer "
    "action listing the COMPLETE revised set of selections (not just the "
    "change), which may mean the same cart plus one more item, a swap, fewer "
    "of something, or something else entirely.\n"
    '4. {"action": "show_menu", "merchant": "<store name, optional>"} — use '
    "this when the buyer asks what's available, what's on the menu, what a "
    "store carries, or wants to browse before deciding anything specific. "
    'Omit "merchant" (or leave it null) to show every store; name one only '
    "if the buyer named a specific store. This displays the real catalog "
    "directly (never invent items or prices yourself) and pauses for the "
    "buyer's next message — you do not need to search first or say "
    "anything else this turn.\n"
    '5. {"action": "show_cart"} — use this when the buyer asks what\'s in '
    "their cart, what they've picked so far, or asks to review the order "
    "before deciding whether to confirm. This displays whatever cart is "
    "already pending (empty if nothing's been built yet) and pauses for "
    "the buyer's next message — never describe cart contents yourself, "
    "this action shows the real thing.\n"
    '6. {"action": "check_order_status"} — use this when the buyer asks '
    "whether their order/payment went through, is still pending, or wants "
    "an update on an order they already placed. This looks up the real "
    "order and pauses for the buyer's next message — never guess or state a "
    "status yourself.\n"
    '7. {"action": "cancel_order"} — use this ONLY when the buyer clearly '
    'and explicitly asks to cancel their order or purchase (e.g. "cancel '
    'my order", "I want to cancel it") — never for a question about '
    'cancellation policy, a hypothetical ("can I cancel later?"), or '
    "cancelling the CURRENT in-progress conversation/cart-building (that is "
    "handled outside you entirely). If genuinely unsure which the buyer "
    "means, use action 2 (ask) to clarify instead of guessing."
)

_CART_CONFIRMATION_QUESTION = (
    "Here's your order — reply \"yes\" to place it, or let me know what you'd like to change."
)

_AFFIRMATIVE_REPLIES = frozenset(
    {
        "yes",
        "y",
        "yep",
        "yeah",
        "yup",
        "confirm",
        "confirmed",
        "place it",
        "place the order",
        "go ahead",
        "continue",
        "do it",
        "sounds good",
        "ok",
        "okay",
        "k",
        "sure",
        "that works",
        "looks good",
        "lgtm",
    }
)

# First-word affirmatives: paired with a trailing-clause check below so
# "yes, go ahead." / "yeah sounds good" / "sure thing" match without having
# to enumerate every phrasing (the original bug: exact-match-only rejected
# "yes, go ahead." — including even "yes." with a trailing period — which
# fell through to the LLM, which re-answered with the SAME cart, which
# paused for confirmation again: an infinite loop bounded only by
# MAX_CONVERSATION_TURNS. See cart_signature() below for a second line of
# defense against exactly this failure mode).
_AFFIRMATIVE_FIRST_WORDS = frozenset(
    {"yes", "y", "yep", "yeah", "yup", "confirm", "confirmed", "sure", "ok", "okay", "k"}
)
# Any of these anywhere in the reply means it's NOT a plain confirmation,
# even if it starts with an affirmative word ("yes but swap the cone").
_REVISION_MARKERS = frozenset(
    {
        "no",
        "not",
        "don't",
        "dont",
        "but",
        "actually",
        "instead",
        "change",
        "wait",
        "except",
        "without",
        "remove",
        "add",
        "swap",
        "replace",
        "also",
    }
)

_PUNCTUATION = ".,!?;:\"'"


def is_affirmative_reply(text: str) -> bool:
    """S16: deterministic, zero-LLM-cost check for the common "yes" case at
    a cart-confirmation pause. Two-tier: an exact match against a whitelist
    of full phrases, OR a first-word affirmative with no revision markers
    anywhere else in the reply ("yes, go ahead." matches; "yes but swap the
    cone" does not — falls through to the LLM instead). Deliberately still
    conservative in the second tier: missing a real confirmation costs one
    harmless extra LLM call (which cart_signature() below will catch even
    then), a false positive would place an unconfirmed order."""
    normalized = text.strip().lower().strip(_PUNCTUATION)
    if normalized in _AFFIRMATIVE_REPLIES:
        return True
    words = [w.strip(_PUNCTUATION) for w in normalized.replace(",", " ").split()]
    if not words or words[0] not in _AFFIRMATIVE_FIRST_WORDS:
        return False
    return not any(w in _REVISION_MARKERS for w in words[1:])


def cart_signature(cart: list[dict[str, Any]]) -> list[tuple[str, str, int]]:
    """Order-independent (merchant_id, sku, qty) fingerprint, used to notice
    when a revision round's LLM answer reproduced the SAME cart it already
    asked about — the buyer's confirming wording wasn't recognized, but there
    was genuinely nothing to revise. A second line of defense against the
    infinite-confirmation-loop failure mode, independent of any wording
    heuristic."""
    return sorted((item["merchant_id"], item["sku"], item["qty"]) for item in cart)


def pending_cart_from_messages(messages: list[dict[str, str]]) -> list[dict[str, Any]] | None:
    """S16: None unless the transcript is currently paused at a cart
    confirmation — i.e. the LAST message is exactly the tool_result marker
    _confirm_cart appends. Used to fast-path a "yes" reply straight to
    checkout without another LLM call, and to tell a cart-confirmation pause
    apart from a plain "ask" pause (which has no such marker)."""
    if not messages:
        return None
    try:
        payload = json.loads(messages[-1].get("content", ""))
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict) and payload.get("tool_result") == "cart_ready":
        cart = payload.get("cart")
        if isinstance(cart, list):
            return cart
    return None


def _most_recent_cart_ready(messages: list[dict[str, str]]) -> list[dict[str, Any]] | None:
    """Like pending_cart_from_messages, but scans the whole transcript
    backward instead of checking only the last message. Needed by show_cart
    (S18): by the time that node runs, messages[-1] is always the buyer's
    new reply followed by the LLM's own action JSON for THIS turn — the
    cart_ready marker from an earlier turn sits further back. A fresh
    "answer" always appends a new marker superseding the old one, so the
    most recent match anywhere in the transcript is always the current
    pending cart."""
    for msg in reversed(messages):
        try:
            payload = json.loads(msg.get("content", ""))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("tool_result") == "cart_ready":
            cart = payload.get("cart")
            if isinstance(cart, list):
                return cart
    return None


async def _agent_step(state: BuyerPlanState, *, config: Settings) -> dict[str, Any]:
    response = llm_chat(
        config,
        messages=[{"role": "system", "content": _SYSTEM_PROMPT}, *state["messages"]],
    )
    return {"messages": [*state["messages"], {"role": "assistant", "content": response}]}


_JSON_DECODER = json.JSONDecoder()


def _parse_agent_action(response: str) -> dict[str, Any]:
    """Parses the LLM's action JSON. Uses raw_decode (parse ONE JSON value,
    ignore what follows) rather than json.loads (require the whole string to
    be valid JSON with nothing else) — live testing found some completions
    append trailing content after a perfectly valid action object (a stray
    sentence, a second JSON blob, trailing whitespace/newlines), which
    json.loads rejects outright as "Extra data" even though the action
    itself parsed fine. Genuinely malformed JSON (the action itself broken)
    still raises — this only tolerates noise AFTER a complete, valid value."""
    stripped = response.strip()
    try:
        draft, end = _JSON_DECODER.raw_decode(stripped)
    except json.JSONDecodeError as e:
        raise BuyerPlanError(f"plan: invalid_json_response: {e}") from e
    trailing = stripped[end:].strip()
    if trailing:
        logger.warning(
            "plan: llm response had trailing content after JSON, ignoring: %r", trailing[:200]
        )
    if not isinstance(draft, dict):
        raise BuyerPlanError(f"plan: invalid_action:{draft!r}")
    if draft.get("action") not in _ACTIONS:
        raise BuyerPlanError(f"plan: invalid_action:{draft.get('action')!r}")
    return draft


def _normalize_search_queries(query: Any) -> list[str]:
    """S17: "query" may be one keyword or a list of them (one search action
    covering several distinct items — see _SYSTEM_PROMPT). Bounded to
    MAX_QUERIES_PER_SEARCH terms (Python-side, not LLM-trusted, R0.5) —
    excess terms are dropped, not a hard failure, since truncating still
    makes forward progress on the buyer's request."""
    if isinstance(query, str):
        queries = [query]
    elif isinstance(query, list) and all(isinstance(q, str) for q in query):
        queries = list(query)
    else:
        raise BuyerPlanError(f"plan: malformed_search_query:{query!r}")
    if not queries:
        raise BuyerPlanError("plan: malformed_search_query: empty")
    if len(queries) > MAX_QUERIES_PER_SEARCH:
        logger.warning(
            "plan: search action requested %d queries, capping to %d",
            len(queries),
            MAX_QUERIES_PER_SEARCH,
        )
        queries = queries[:MAX_QUERIES_PER_SEARCH]
    return queries


async def _campaign_offers(mcp: Any) -> dict[tuple[str, str], dict[str, Any]]:
    """Index the merchant's live offers by (merchant_id, sku).

    This is how a buyer agent discovers a campaign at all — before it, the
    orchestrator published into a void: no cart line ever carried a campaign_id,
    so compiler check 12 and the discount math were unreachable in production.

    The LLM is NOT told to pick offers, and there is no new plan action. Python
    attaches campaign_id to whatever the model selects (R0.8/R0.9 applied to
    marketing): a model cannot invent a discount, and if it somehow named a
    stale one the compiler would reject the cart at check 12 anyway.

    A merchant with no campaigns, or an origin that fails, contributes nothing.
    """
    lister = getattr(mcp, "list_campaigns", None)
    if lister is None:
        return {}
    result = await lister()
    if not result.get("success"):
        return {}

    offers: dict[tuple[str, str], dict[str, Any]] = {}
    for campaign in result.get("data", {}).get("campaigns", []):
        campaign_merchant = campaign.get("merchant_id")
        for sku in campaign.get("applies_to_skus", []):
            # First offer wins, so a SKU covered by two campaigns is stable
            # across turns rather than flipping on dict ordering.
            offers.setdefault(
                (campaign_merchant, sku),
                {
                    "campaign_id": campaign["campaign_id"],
                    "title": campaign.get("title", ""),
                    "discount_bps": campaign.get("discount_bps", 0),
                },
            )
    return offers


async def _run_search(state: BuyerPlanState, *, mcp: Any, config: Settings) -> dict[str, Any]:
    draft = _parse_agent_action(state["messages"][-1]["content"])
    queries = _normalize_search_queries(draft.get("query", ""))
    tags = draft.get("tags")
    if tags is not None and not isinstance(tags, list):
        raise BuyerPlanError(f"plan: malformed_search_tags:{tags!r}")

    on_search = state.get("on_search")
    offers = await _campaign_offers(mcp)
    merged = dict(state["all_search_results"])
    results_by_query: dict[str, list[dict[str, Any]]] = {}
    for query in queries:
        if on_search is not None:
            await on_search(query)
        search_result = await mcp.search_products(query, tags=tags, limit=20)
        items = (search_result.get("data") or {}).get("items", [])
        results_by_query[query] = items
        for item in items:
            # A federating client stamps its own merchant_id per item, which
            # must win over the single-merchant fallback. Resolve the fallback
            # lazily and only when an item actually lacks one: a buyer-process
            # config has no single `merchant` at all, so computing it eagerly
            # (as setdefault's argument would) crashes the federated path on
            # items that never needed it.
            if "merchant_id" not in item:
                item["merchant_id"] = merchant_id(config)
            offer = offers.get((item["merchant_id"], item["sku"]))
            if offer is not None:
                item["campaign_id"] = offer["campaign_id"]
                item["campaign_title"] = offer["title"]
                item["discount_bps"] = offer["discount_bps"]
            merged[f"{item['merchant_id']}::{item['sku']}"] = item

    tool_result_message = {
        "role": "user",
        "content": json.dumps({"tool_result": "search", "results": results_by_query}),
    }
    return {
        "messages": [*state["messages"], tool_result_message],
        "all_search_results": merged,
        "tool_calls_used": state["tool_calls_used"] + 1,
    }


def _rebuild_search_results_from_messages(
    messages: list[dict[str, str]],
) -> dict[str, dict[str, Any]]:
    """Scans a transcript for prior search tool-result turns (the JSON
    messages _run_search appends) and unions their items by sku. Used to
    seed converse()'s all_search_results so a resumed turn doesn't lose
    what earlier turns already found.

    S17: a search tool-result's items live under "results" (a dict keyed by
    each query term, since one search action can now cover several terms) —
    this flattens across every term/message.

    S12: keyed by "merchant_id::sku", not bare sku — merchant_id is already
    serialized into the transcript JSON by _run_search, so this only changes
    the merge key (no config/merchant_id() call needed here).

    Revision turns ("I also want X"): the prompt tells the model to answer
    with the COMPLETE revised set, but search-result messages from earlier
    turns may no longer be in the persisted transcript while the cart_ready
    marker is. The cart was validated when built and its lines carry
    server-stamped unit_minor/tags/name, so the most recent pending cart
    counts as known-good provenance too — otherwise every add-to-cart
    follow-up fails with hallucinated_sku on its own old lines. Fresh
    search data wins where both exist."""
    merged: dict[str, dict[str, Any]] = {}
    for msg in messages:
        try:
            payload = json.loads(msg.get("content", ""))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("tool_result") == "search":
            for items in payload.get("results", {}).values():
                for item in items:
                    merged[f"{item['merchant_id']}::{item['sku']}"] = item
    for line in _most_recent_cart_ready(messages) or []:
        if isinstance(line, dict) and line.get("merchant_id") and line.get("sku"):
            merged.setdefault(f"{line['merchant_id']}::{line['sku']}", line)
    return merged


def _validate_selection(state: BuyerPlanState) -> dict[str, Any]:
    draft = _parse_agent_action(state["messages"][-1]["content"])
    selections = draft.get("selections")
    if not isinstance(selections, list):
        raise BuyerPlanError("plan: malformed_selection: 'selections' is not a list")

    by_key = state["all_search_results"]
    cart: list[dict[str, Any]] = []
    for entry in selections:
        if not isinstance(entry, dict):
            raise BuyerPlanError(f"plan: malformed_selection: {entry!r}")
        sku = entry.get("sku")
        entry_merchant_id = entry.get("merchant_id")
        qty = entry.get("qty")
        if (
            not isinstance(sku, str)
            or not isinstance(entry_merchant_id, str)
            or not entry_merchant_id
            or not isinstance(qty, int)
            or qty < 1
        ):
            raise BuyerPlanError(f"plan: malformed_selection: {entry!r}")
        # Composite key: two stores can both sell the same bare sku (R0.5 —
        # a missing/wrong merchant_id must fail loud here, not silently
        # validate against the wrong store's price).
        key = f"{entry_merchant_id}::{sku}"
        if key not in by_key:
            raise BuyerPlanError(f"plan: hallucinated_sku:{sku}")

        source = by_key[key]
        line = {
            "sku": sku,
            "merchant_id": entry_merchant_id,
            "qty": qty,
            "unit_minor": source["unit_minor"],
            "tags": source.get("tags", []),
            "name": source.get("name", sku),
        }
        # The campaign comes from the search result Python stamped, never from
        # the model's selection payload — an LLM cannot name a discount into
        # existence, and the compiler re-checks validity at check 12 regardless.
        if source.get("campaign_id"):
            line["campaign_id"] = source["campaign_id"]
            line["campaign_title"] = source.get("campaign_title", "")
            line["discount_bps"] = source.get("discount_bps", 0)
        cart.append(line)

    return {"cart": cart}


def _confirm_cart(state: BuyerPlanState) -> dict[str, Any]:
    """S16: a non-empty cart pauses for explicit buyer confirmation instead
    of ending the turn — the buyer must see it and say "yes" before checkout
    is ever submitted (R0.9: nothing the LLM proposes moves money on its
    own). Appends the same tool_result marker convention _run_search uses,
    so pending_cart_from_messages() can recognize this pause on the next
    turn and is_affirmative_reply() can fast-path a plain "yes" without
    another LLM call."""
    cart_ready_message = {
        "role": "user",
        "content": json.dumps({"tool_result": "cart_ready", "cart": state["cart"]}),
    }
    return {
        "messages": [*state["messages"], cart_ready_message],
        "pending_question": _CART_CONFIRMATION_QUESTION,
    }


def _route_after_validate_selection(state: BuyerPlanState) -> str:
    # An empty selections list ("nothing fit the goal") has nothing to
    # confirm — end immediately, same as before S16.
    return "confirm_cart" if state["cart"] else "end"


def _give_up_after_cap(state: BuyerPlanState) -> dict[str, Any]:
    return {"pending_question": _NO_MATCH_AFTER_SEARCH_QUESTION}


def _set_pending_question(state: BuyerPlanState) -> dict[str, Any]:
    draft = _parse_agent_action(state["messages"][-1]["content"])
    message = draft.get("message")
    if not isinstance(message, str) or not message:
        raise BuyerPlanError(f"plan: malformed_ask_message:{message!r}")
    return {"pending_question": message}


_SHOW_MENU_QUESTION = "Take a look — what would you like?"
_NOTHING_PICKED_YET_QUESTION = (
    "You haven't picked anything yet — tell me what you're looking for and I'll build a cart!"
)


async def _show_menu(state: BuyerPlanState) -> dict[str, Any]:
    """show_menu action (S18): the actual catalog fetch + embed rendering
    live in buyer_agent.py (Discord-specific) — this node only validates the
    LLM's draft and fires the callback, same division of labor as on_search."""
    draft = _parse_agent_action(state["messages"][-1]["content"])
    merchant_filter = draft.get("merchant")
    if merchant_filter is not None and not isinstance(merchant_filter, str):
        raise BuyerPlanError(f"plan: malformed_show_menu_merchant:{merchant_filter!r}")
    on_menu_ready = state.get("on_menu_ready")
    if on_menu_ready is not None:
        await on_menu_ready(merchant_filter or None)
    shown_message = {"role": "user", "content": json.dumps({"tool_result": "menu_shown"})}
    return {
        "messages": [*state["messages"], shown_message],
        "pending_question": _SHOW_MENU_QUESTION,
    }


async def _show_cart(state: BuyerPlanState) -> dict[str, Any]:
    """show_cart action (S18): reads whatever cart is already pending from
    this transcript (_most_recent_cart_ready — messages[-1] here is always
    this turn's own action JSON, so the plain last-message check
    pending_cart_from_messages does won't find it) — never state["cart"],
    so this can never trip continue_shop's cart_signature safety net (which
    exists to auto-submit an UNCHANGED cart after an unrecognized
    confirmation) into treating "what's in my cart?" as a confirmation."""
    cart = _most_recent_cart_ready(state["messages"]) or []
    on_cart_shown = state.get("on_cart_shown")
    if on_cart_shown is not None:
        await on_cart_shown(cart)
    shown_message = {"role": "user", "content": json.dumps({"tool_result": "cart_shown"})}
    return {
        "messages": [*state["messages"], shown_message],
        "pending_question": _CART_CONFIRMATION_QUESTION if cart else _NOTHING_PICKED_YET_QUESTION,
    }


_ANYTHING_ELSE_QUESTION = "Anything else I can help with?"


async def _check_order_status(state: BuyerPlanState) -> dict[str, Any]:
    """check_order_status action (S14): the actual list_orders fan-out and
    rendering happen in buyer_agent.py (needs the buyer's chat identity,
    which this state never carries, and mcp access, which only _run_search
    touches directly among these nodes) — this node only fires the callback
    and pauses."""
    on_order_status = state.get("on_order_status")
    if on_order_status is not None:
        await on_order_status()
    shown_message = {
        "role": "user",
        "content": json.dumps({"tool_result": "order_status_shown"}),
    }
    return {
        "messages": [*state["messages"], shown_message],
        "pending_question": _ANYTHING_ELSE_QUESTION,
    }


async def _cancel_order_action(state: BuyerPlanState) -> dict[str, Any]:
    """cancel_order action (S14): same division of labor as
    _check_order_status — buyer_agent.py owns the list_orders fan-out,
    zero/one/many-HELD-orders branching, and the actual cancel_order MCP
    call. Never touches state["cart"] (nothing here could ever be mistaken
    for a cart-confirmation pause)."""
    on_cancel_requested = state.get("on_cancel_requested")
    if on_cancel_requested is not None:
        await on_cancel_requested()
    shown_message = {
        "role": "user",
        "content": json.dumps({"tool_result": "cancel_requested"}),
    }
    return {
        "messages": [*state["messages"], shown_message],
        "pending_question": _ANYTHING_ELSE_QUESTION,
    }


def _route_after_agent_step(state: BuyerPlanState) -> str:
    draft = _parse_agent_action(state["messages"][-1]["content"])
    action = draft["action"]
    if action == "search":
        return "run_search"
    if action == "ask":
        return "set_pending_question"
    if action == "show_menu":
        return "show_menu"
    if action == "show_cart":
        return "show_cart"
    if action == "check_order_status":
        return "check_order_status"
    if action == "cancel_order":
        return "cancel_order"
    return "validate_selection"


def _route_after_search(state: BuyerPlanState) -> str:
    # Cap checked HERE, right after a search actually runs — not on the next
    # agent_step entry — so hitting the cap never costs a "bonus" LLM call
    # asking a question we're just going to ignore (R0.5: bounded, no waste).
    if state["tool_calls_used"] >= MAX_TOOL_CALLS_PER_TURN:
        return "give_up_after_cap"
    return "agent_step"


class BuyerGraph:
    """LangGraph-backed buyer planning loop: agent_step <-> run_search, with
    agent_step also able to route to set_pending_question (LLM asks the
    buyer something) or validate_selection (LLM finalizes a cart). Built once
    per instance; ainvoke()'d per converse()."""

    def __init__(self, config: Settings, mcp_client: Any):
        self.config = config
        self.mcp = mcp_client

        async def _agent_step_node(state: BuyerPlanState) -> dict[str, Any]:
            return await _agent_step(state, config=self.config)

        async def _run_search_node(state: BuyerPlanState) -> dict[str, Any]:
            return await _run_search(state, mcp=self.mcp, config=self.config)

        graph: StateGraph[BuyerPlanState, None, BuyerPlanState, BuyerPlanState] = StateGraph(
            BuyerPlanState
        )
        graph.add_node("agent_step", _agent_step_node)
        graph.add_node("run_search", _run_search_node)
        graph.add_node("give_up_after_cap", _give_up_after_cap)
        graph.add_node("set_pending_question", _set_pending_question)
        graph.add_node("validate_selection", _validate_selection)
        graph.add_node("confirm_cart", _confirm_cart)
        graph.add_node("show_menu", _show_menu)
        graph.add_node("show_cart", _show_cart)
        graph.add_node("check_order_status", _check_order_status)
        graph.add_node("cancel_order", _cancel_order_action)

        graph.add_edge(START, "agent_step")
        graph.add_conditional_edges(
            "agent_step",
            _route_after_agent_step,
            {
                "run_search": "run_search",
                "set_pending_question": "set_pending_question",
                "validate_selection": "validate_selection",
                "show_menu": "show_menu",
                "show_cart": "show_cart",
                "check_order_status": "check_order_status",
                "cancel_order": "cancel_order",
            },
        )
        graph.add_conditional_edges(
            "run_search",
            _route_after_search,
            {"agent_step": "agent_step", "give_up_after_cap": "give_up_after_cap"},
        )
        graph.add_conditional_edges(
            "validate_selection",
            _route_after_validate_selection,
            {"confirm_cart": "confirm_cart", "end": END},
        )
        graph.add_edge("give_up_after_cap", END)
        graph.add_edge("set_pending_question", END)
        graph.add_edge("confirm_cart", END)
        graph.add_edge("show_menu", END)
        graph.add_edge("show_cart", END)
        graph.add_edge("check_order_status", END)
        graph.add_edge("cancel_order", END)

        self._graph = graph.compile()

    async def converse(
        self,
        messages: list[dict[str, str]],
        policy_id: str,
        trace_id: str,
        on_search: Callable[[str], Awaitable[None]] | None = None,
        on_menu_ready: Callable[[str | None], Awaitable[None]] | None = None,
        on_cart_shown: Callable[[list[dict[str, Any]]], Awaitable[None]] | None = None,
        on_order_status: Callable[[], Awaitable[None]] | None = None,
        on_cancel_requested: Callable[[], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Runs the agent loop from an arbitrary starting transcript. Used
        directly for a resumed conversation turn; plan() is a thin wrapper
        for a fresh goal. Returns either {"cart": [], "messages": [...]}
        (nothing fit the goal at all — this is the ONLY case a bare "cart"
        is returned) or {"awaiting_reply": True, "question": ..., "cart": ...,
        "messages": [...]} — "cart" is non-empty exactly when this pause is a
        cart-confirmation (S16: "answer" never submits a cart directly
        anymore, it always pauses for buyer confirmation first) and empty
        for a plain "ask" pause. Callers tell the two pause kinds apart by
        checking whether "cart" is non-empty.

        all_search_results is rebuilt from any search tool-result turns
        already present in `messages` (a resumed conversation's earlier
        turns) — not just searches run during THIS call. The LLM still
        "remembers" earlier search results from the transcript it can read;
        without this, validate_selection would reject a sku it correctly
        recalled from an earlier turn as "hallucinated" simply because this
        invocation didn't search for it again.

        on_search, when given, is awaited once per search with the query
        (S15: "Looking for X…" chat feedback) — carried through per-call
        state, not stored on self, since one BuyerGraph instance is shared
        across concurrent buyers."""
        initial_state: BuyerPlanState = {
            "goal": messages[0]["content"] if messages else "",
            "policy_id": policy_id,
            "trace_id": trace_id,
            "messages": messages,
            "all_search_results": _rebuild_search_results_from_messages(messages),
            "tool_calls_used": 0,
            "cart": [],
            "on_search": on_search,
            "on_menu_ready": on_menu_ready,
            "on_cart_shown": on_cart_shown,
            "on_order_status": on_order_status,
            "on_cancel_requested": on_cancel_requested,
        }
        result_state = await self._graph.ainvoke(initial_state)

        if result_state.get("pending_question"):
            return {
                "awaiting_reply": True,
                "question": result_state["pending_question"],
                "cart": result_state.get("cart") or [],
                "messages": result_state["messages"],
            }
        return {"cart": result_state["cart"], "messages": result_state["messages"]}

    async def plan(
        self,
        goal: str,
        policy_id: str,
        trace_id: str,
        on_search: Callable[[str], Awaitable[None]] | None = None,
        on_menu_ready: Callable[[str | None], Awaitable[None]] | None = None,
        on_cart_shown: Callable[[list[dict[str, Any]]], Awaitable[None]] | None = None,
        on_order_status: Callable[[], Awaitable[None]] | None = None,
        on_cancel_requested: Callable[[], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        notifier = DiscordNotifier(self.config)
        await notifier.buyer_trace(trace_id, "plan_start", {"goal": goal, "policy_id": policy_id})

        result = await self.converse(
            [{"role": "user", "content": goal}],
            policy_id,
            trace_id,
            on_search,
            on_menu_ready,
            on_cart_shown,
            on_order_status,
            on_cancel_requested,
        )

        if result.get("awaiting_reply"):
            await notifier.buyer_trace(
                trace_id, "plan_awaiting_reply", {"question": result["question"]}
            )
        else:
            await notifier.buyer_trace(
                trace_id, "plan_done", {"items": len(result["cart"]), "policy_id": policy_id}
            )
        return result
