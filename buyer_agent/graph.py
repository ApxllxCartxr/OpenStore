import httpx
import json
import re
import sqlite3
from typing import TypedDict

from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

from buyer_agent.llm import call_gemini_structured, call_gemini_text
from buyer_agent.mcp_client import call_mcp_tool


class ConversationState(TypedDict, total=False):
    conversation_id: str
    buyer_id: str
    merchant_url: str
    mcp_endpoint: str
    token: str
    last_user_message: str
    intent: str
    search_results: list[dict]
    cross_sell_suggestion: str
    cart_id: int | None
    cart_version: int | None
    cart_summary: str
    pending_checkout_id: str | None
    order_result: dict | None
    reply_text: str
    delivery_address: str | None


_INTENT_KEYWORDS = {
    "checkout": r"\b(checkout|pay|buy|purchase|order|place order)\b",
    "add_to_cart": r"\b(add|cart|put|get me|I want|I'd like|give me)\b",
    "browse": r"\b(search|find|show|look|browse|what do you have|list|catalog|menu)\b",
}

DEFAULT_DELIVERY_ADDRESS = "221B Baker Street"

_VALID_INTENTS = {"browse", "add_to_cart", "checkout", "general_question"}


class IntentClassification(BaseModel):
    """Structured output schema for Gemini intent classification."""

    intent: str = Field(
        description="One of: browse, add_to_cart, checkout, general_question",
    )


def _keyword_classify(state: ConversationState) -> str:
    """Regex-based fallback classifier."""
    msg = state["last_user_message"].lower()
    for intent, pattern in _INTENT_KEYWORDS.items():
        if re.search(pattern, msg):
            return intent
    return "general"


def _extract_delivery_address(msg: str) -> str | None:
    """Extract delivery address from user message if present.
    Looks for patterns like 'deliver to', 'address', 'ship to' followed by address."""
    import re

    patterns = [
        r"deliver\s+to\s+(.+?)(?:\.|$|,)",
        r"address\s+(?:is\s+)?(.+?)(?:\.|$|,)",
        r"ship\s+to\s+(.+?)(?:\.|$|,)",
        r"send\s+to\s+(.+?)(?:\.|$|,)",
    ]
    for pattern in patterns:
        match = re.search(pattern, msg, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def classify_node(state: ConversationState) -> dict:
    """Calls Gemini with state['last_user_message'] + minimal cart/checkout
    context, asking it to classify intent into one of: browse, add_to_cart,
    checkout, general_question. Uses constrained/structured output, validated
    against a Pydantic model. Falls back to keyword matching on any parse or
    API failure. Returns {'intent': <str>}."""
    cart_summary = state.get("cart_summary", "")
    pending = state.get("pending_checkout_id") is not None

    prompt = (
        "Classify the user's intent for an ice cream shop assistant.\n\n"
        f'User message: "{state["last_user_message"]}"\n'
    )
    if cart_summary:
        prompt += f"Current cart: {cart_summary}\n"
    if pending:
        prompt += "A checkout is currently pending.\n"
    prompt += (
        "\nReturn exactly one of: browse, add_to_cart, checkout, general_question.\n"
        "- browse: user wants to see products, search, or look around\n"
        "- add_to_cart: user wants to add items to their cart\n"
        "- checkout: user wants to pay, buy, or complete their order\n"
        "- general_question: anything else (greeting, hours, policies, etc.)"
    )

    result = call_gemini_structured(prompt, IntentClassification)
    if result is not None and result.intent in _VALID_INTENTS:
        updates = {"intent": result.intent}
        address = _extract_delivery_address(state["last_user_message"])
        if address:
            updates["delivery_address"] = address
        return updates

    updates = {"intent": _keyword_classify(state)}
    address = _extract_delivery_address(state["last_user_message"])
    if address:
        updates["delivery_address"] = address
    return updates


def search_node(state: ConversationState) -> dict:
    """Search the merchant's catalog via MCP tool."""
    msg = state["last_user_message"]
    results = call_mcp_tool(
        state["mcp_endpoint"], "search_products", {"query": msg}, state["token"]
    )
    content = results.get("result", {}).get("content", [])
    products = []
    for c in content:
        try:
            parsed = json.loads(c.get("text", "[]"))
            if isinstance(parsed, list):
                products.extend(parsed)
            else:
                products.append(parsed)
        except (ValueError, KeyError):
            pass
    return {"search_results": products}


def consult_merchant_agent_node(state: ConversationState) -> dict:
    """Call the merchant reasoning agent over A2A for a cross-sell suggestion.
    STUBBED today — returns a fixed placeholder; wired to real A2A on Day 6."""
    cart = state.get("cart_summary", "")
    if cart:
        suggestion = (
            "Based on your cart, you might also enjoy our Mango Sorbetto "
            "(dairy-free, fruit) — a perfect complement."
        )
    else:
        suggestion = (
            "Our most popular item is the Pistachio Gelato — "
            "Sicilian pistachio, no artificial color."
        )
    return {"cross_sell_suggestion": suggestion}


def summarize_cart_node(state: ConversationState) -> dict:
    """Create or update the cart, then format its contents for display."""
    search_results = state.get("search_results", [])
    msg = state["last_user_message"].lower()

    if state.get("cart_id") is not None and (
        "add" in msg or "cart" in msg or "want" in msg or "like" in msg
    ):
        items = _extract_items_from_message(msg, search_results)
        if items:
            result = call_mcp_tool(
                state["mcp_endpoint"],
                "update_cart",
                {"cart_id": state["cart_id"], "items": items},
                state["token"],
            )
            content = result.get("result", {}).get("content", [])
            for c in content:
                try:
                    parsed = json.loads(c.get("text", "{}"))
                    if isinstance(parsed, dict) and "cart_id" in parsed:
                        return {
                            "cart_id": parsed["cart_id"],
                            "cart_version": parsed.get("version"),
                            "cart_summary": _format_cart(parsed.get("items", [])),
                        }
                except (ValueError, KeyError):
                    pass

    items = _extract_items_from_message(msg, search_results)
    if items:
        result = call_mcp_tool(
            state["mcp_endpoint"], "create_cart", {"items": items}, state["token"]
        )
        content = result.get("result", {}).get("content", [])
        for c in content:
            try:
                parsed = json.loads(c.get("text", "{}"))
                if isinstance(parsed, dict) and "cart_id" in parsed:
                    return {
                        "cart_id": parsed["cart_id"],
                        "cart_version": parsed.get("version"),
                        "cart_summary": _format_cart(parsed.get("items", [])),
                    }
            except (ValueError, KeyError):
                pass

    return {"cart_summary": "No items in cart yet."}


def checkout_node(state: ConversationState) -> dict:
    """Call checkout_initiate then checkout_confirm with Intent Compiler path."""
    cart_id = state.get("cart_id")
    if cart_id is None:
        return {
            "order_result": {"error": "No cart to checkout"},
            "reply_text": "You don't have anything in your cart yet.",
        }

    delivery_address = state.get("delivery_address") or DEFAULT_DELIVERY_ADDRESS

    initiate_result = call_mcp_tool(
        state["mcp_endpoint"],
        "checkout_initiate",
        {"cart_id": cart_id, "delivery_address": delivery_address},
        state["token"],
    )
    content = initiate_result.get("result", {}).get("content", [])
    checkout_id = None
    for c in content:
        try:
            parsed = json.loads(c.get("text", "{}"))
            if isinstance(parsed, dict) and "checkout_id" in parsed:
                checkout_id = parsed["checkout_id"]
                break
        except (ValueError, KeyError):
            pass

    if checkout_id is None:
        error_msg = "Checkout initiation failed"
        for c in content:
            try:
                parsed = json.loads(c.get("text", "{}"))
                if isinstance(parsed, dict) and "error" in parsed:
                    error_msg = parsed["error"]
                    break
            except (ValueError, KeyError):
                pass
        return {"order_result": {"error": error_msg}, "reply_text": error_msg}

    # Fetch the latest stored policy token using authenticated HTTP call
    try:
        assertion_url = state["mcp_endpoint"].replace(
            "/agent/mcp", "/internal/webauthn/latest-assertion"
        )
        resp = httpx.get(
            assertion_url,
            headers={"Authorization": f"Bearer {state['token']}"},
            timeout=10.0,
        )
        resp.raise_for_status()
        policy_data = resp.json()
        policy_token = policy_data["policy_token"]
        policy_json = policy_data["policy_json"]
    except Exception:
        return {
            "order_result": {"error": "No signed policy found — sign a policy first"},
            "reply_text": "No signed policy found. Please sign an intent policy first.",
        }

    confirm_result = call_mcp_tool(
        state["mcp_endpoint"],
        "checkout_confirm",
        {
            "checkout_id": checkout_id,
            "policy_token": policy_token,
            "policy_json": policy_json,
            "idempotency_key": f"buyer-{checkout_id}",
        },
        state["token"],
    )
    confirm_content = confirm_result.get("result", {}).get("content", [])
    for c in confirm_content:
        try:
            parsed = json.loads(c.get("text", "{}"))
            if isinstance(parsed, dict):
                return {"order_result": parsed, "pending_checkout_id": checkout_id}
        except (ValueError, KeyError):
            pass

    error_detail = confirm_result.get("error", {})
    if error_detail:
        error_msg = error_detail.get("message", "Checkout confirmation failed")
        return {"order_result": {"error": error_msg}, "reply_text": error_msg}

    return {
        "order_result": {"error": "Checkout confirmation failed"},
        "reply_text": "Checkout confirmation failed.",
    }


def _fallback_explain(state: ConversationState) -> str:
    """Template-string fallback when Gemini is unavailable."""
    order = state.get("order_result")
    if order is None:
        return "Something went wrong — no result to report."

    if "error" in order:
        return f"Error: {order['error']}"

    if "payment_link_url" in order:
        return (
            f"Order confirmed! Your payment link: {order['payment_link_url']}\n"
            f"Total verified by the Intent Compiler against your signed policy."
        )

    if order.get("status") == "REJECTED":
        return f"Order rejected by the Intent Compiler: {order.get('reason', 'policy violation')}"

    return f"Order status: {order.get('status', 'unknown')}"


def explain_node(state: ConversationState) -> dict:
    """Calls Gemini with the final state (order_result, cart_summary,
    cross_sell_suggestion, or rejection detail — whichever are present) and
    a system instruction to phrase ONLY the facts given, never invent
    figures/SKUs/status not present in state. Returns {'reply_text': str}.
    Falls back to a plain template string on API failure — a demo should
    never go silent because Gemini's API had a bad moment."""
    order = state.get("order_result")
    if order is None:
        return {"reply_text": "Something went wrong — no result to report."}

    facts = []
    if "error" in order:
        facts.append(f"Error occurred: {order['error']}")
    elif "payment_link_url" in order:
        facts.append(f"Order confirmed. Payment link: {order['payment_link_url']}")
        facts.append(
            "Total was verified by the Intent Compiler against the signed policy."
        )
    elif order.get("status") == "REJECTED":
        facts.append(
            f"Order rejected by the Intent Compiler: {order.get('reason', 'policy violation')}"
        )
    else:
        facts.append(f"Order status: {order.get('status', 'unknown')}")

    cart = state.get("cart_summary", "")
    if cart:
        facts.append(f"Cart contents: {cart}")

    cross_sell = state.get("cross_sell_suggestion", "")
    if cross_sell:
        facts.append(f"Cross-sell suggestion: {cross_sell}")

    prompt = (
        "You are a friendly ice cream shop assistant writing a Discord reply.\n"
        "You MUST only use the facts provided below. Do NOT invent any prices, "
        "SKUs, order IDs, or statuses that are not explicitly stated.\n"
        "Keep it concise (2-4 sentences). Be warm but factual.\n\n"
        "Facts:\n" + "\n".join(f"- {f}" for f in facts)
    )

    text = call_gemini_text(prompt)
    if text is not None and text.strip():
        return {"reply_text": text.strip()}

    return {"reply_text": _fallback_explain(state)}


def _extract_items_from_message(msg: str, search_results: list[dict]) -> list[dict]:
    """Best-effort extraction of items to add to cart from the user message
    and search results. Returns list of {sku, qty}."""
    items = []
    for product in search_results:
        name = product.get("name", "").lower()
        sku = product.get("sku", "")
        if any(word in msg for word in name.split() if len(word) > 2):
            items.append({"sku": sku, "qty": 1})
    if not items and search_results:
        items = [{"sku": search_results[0]["sku"], "qty": 1}]
    return items


def _format_cart(items: list[dict]) -> str:
    if not items:
        return "Empty cart"
    lines = []
    total = 0
    for item in items:
        price = item.get("unit_minor", 0) / 100
        qty = item.get("qty", 1)
        total += price * qty
        lines.append(f"- {item.get('sku', 'unknown')} x{qty} @ ₹{price:.2f}")
    lines.append(f"**Total: ₹{total:.2f}**")
    return "\n".join(lines)


def _route_after_summarize(state: ConversationState) -> str:
    """Skip checkout if the user didn't ask to buy anything."""
    if state.get("intent") == "checkout":
        return "checkout"
    return "explain"


def build_graph(checkpoint_path: str = "buyer_agent_state.db"):
    """Build and compile the LangGraph state machine.

    The graph uses conditional routing after summarization: only 'checkout' intent
    proceeds to the checkout node; other intents go directly to explain. This is
    the correct behavior — we don't want to initiate checkout on every add-to-cart
    or browse request.
    """
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
    graph.add_conditional_edges(
        "summarize",
        _route_after_summarize,
        {
            "checkout": "checkout",
            "explain": "explain",
        },
    )
    graph.add_edge("checkout", "explain")
    graph.add_edge("explain", END)

    graph.set_entry_point("classify")

    conn = sqlite3.connect(checkpoint_path)
    checkpointer = SqliteSaver(conn)
    return graph.compile(checkpointer=checkpointer)
