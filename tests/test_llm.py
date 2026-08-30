"""Tests for buyer agent LLM integration — classify_node and explain_node.

All tests mock the Gemini client so CI doesn't need a real API key.
Fallback paths are explicitly exercised.
"""

from unittest.mock import patch

from reference.buyer_agent.graph import (
    IntentClassification,
    classify_node,
    explain_node,
    _keyword_classify,
    _fallback_explain,
)


def _state(**overrides):
    base = {
        "last_user_message": "show me the menu",
        "conversation_id": "ch-1",
        "buyer_id": "user-1",
        "merchant_url": "http://localhost:8000",
        "mcp_endpoint": "http://localhost:8000/agent/mcp",
        "token": "tok",
    }
    base.update(overrides)
    return base


# --- classify_node ---


@patch("reference.buyer_agent.graph.call_gemini_structured")
def test_classify_node_uses_gemini_when_available(mock_gemini):
    mock_gemini.return_value = IntentClassification(intent="browse")
    result = classify_node(_state(last_user_message="what flavors do you have"))
    assert result == {"intent": "browse"}
    mock_gemini.assert_called_once()


@patch("reference.buyer_agent.graph.call_gemini_structured")
def test_classify_node_falls_back_on_api_failure(mock_gemini):
    mock_gemini.return_value = None
    result = classify_node(_state(last_user_message="buy me some gelato"))
    assert result == {"intent": "checkout"}


@patch("reference.buyer_agent.graph.call_gemini_structured")
def test_classify_node_falls_back_on_invalid_intent(mock_gemini):
    mock_gemini.return_value = IntentClassification(intent=" INVALID ")
    result = classify_node(_state(last_user_message="add pistachio to cart"))
    assert result == {"intent": "add_to_cart"}


@patch("reference.buyer_agent.graph.call_gemini_structured")
def test_classify_node_passes_cart_context(mock_gemini):
    mock_gemini.return_value = IntentClassification(intent="checkout")
    state = _state(
        last_user_message="buy it",
        cart_summary="- gel-001 x2 @ ₹150.00",
        pending_checkout_id="chk_123",
    )
    result = classify_node(state)
    assert result == {"intent": "checkout"}
    prompt_arg = mock_gemini.call_args[0][0]
    assert "gel-001" in prompt_arg
    assert "checkout" in prompt_arg.lower() or "pending" in prompt_arg.lower()


def test_keyword_classify_returns_general_for_unmatched():
    state = _state(last_user_message="hello there")
    assert _keyword_classify(state) == "general"


def test_keyword_classify_detects_checkout():
    state = _state(last_user_message="I want to purchase this")
    assert _keyword_classify(state) == "checkout"


# --- explain_node ---


@patch("reference.buyer_agent.graph.call_gemini_text")
def test_explain_node_uses_gemini_when_available(mock_gemini):
    mock_gemini.return_value = "Great news! Your order is confirmed."
    state = _state(
        order_result={"payment_link_url": "https://rzp.io/abc123"},
        cart_summary="- gel-001 x1 @ ₹150.00",
    )
    result = explain_node(state)
    assert result == {"reply_text": "Great news! Your order is confirmed."}
    mock_gemini.assert_called_once()


@patch("reference.buyer_agent.graph.call_gemini_text")
def test_explain_node_falls_back_on_api_failure(mock_gemini):
    mock_gemini.return_value = None
    state = _state(
        order_result={"payment_link_url": "https://rzp.io/abc123"},
    )
    result = explain_node(state)
    assert "rzp.io/abc123" in result["reply_text"]


@patch("reference.buyer_agent.graph.call_gemini_text")
def test_explain_node_falls_back_on_empty_response(mock_gemini):
    mock_gemini.return_value = "   "
    state = _state(order_result={"error": "something broke"})
    result = explain_node(state)
    assert result == {"reply_text": "Error: something broke"}


def test_explain_node_returns_early_when_no_order_result():
    result = explain_node(_state())
    assert result == {"reply_text": "Something went wrong — no result to report."}


@patch("reference.buyer_agent.graph.call_gemini_text")
def test_explain_node_rejection_fallback(mock_gemini):
    mock_gemini.return_value = None
    state = _state(
        order_result={"status": "REJECTED", "reason": "item not vegan"},
    )
    result = explain_node(state)
    assert "rejected" in result["reply_text"].lower()
    assert "not vegan" in result["reply_text"]


def test_fallback_explain_error_path():
    state = _state(order_result={"error": "rate limited"})
    assert _fallback_explain(state) == "Error: rate limited"


def test_fallback_explain_rejection_path():
    state = _state(order_result={"status": "REJECTED", "reason": "over budget"})
    text = _fallback_explain(state)
    assert "rejected" in text.lower()
    assert "over budget" in text


@patch("reference.buyer_agent.graph.call_gemini_text")
def test_explain_node_includes_cart_and_cross_sell_in_prompt(mock_gemini):
    mock_gemini.return_value = "Your cart is ready!"
    state = _state(
        order_result={"payment_link_url": "https://rzp.io/xyz"},
        cart_summary="- gel-001 x1 @ ₹150.00",
        cross_sell_suggestion="Try our Mango Sorbetto!",
    )
    explain_node(state)
    prompt_arg = mock_gemini.call_args[0][0]
    assert "gel-001" in prompt_arg
    assert "Mango Sorbetto" in prompt_arg
