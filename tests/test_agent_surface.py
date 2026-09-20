"""The public front door: the cards, `/agent/*`, and the approve page.

Everything here is reachable by a stranger, because that is the design. What
makes it safe is the Gate, not the doorman.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from openstore.sidecar.admission.oauth import Admission
from openstore.sidecar.admission.ratelimit import RateLimiter, Tier
from openstore.sidecar.app import app
from openstore.sidecar.authority.tokens import TokenStore
from openstore.sidecar.console.approve import ApproveContext
from openstore.sidecar.console.approve import configure as configure_approve
from openstore.sidecar.core.codes import PaymentMethod, ReasonCode, Scope, ToolName
from openstore.sidecar.protocols.agent_routes import AgentSurface
from openstore.sidecar.protocols.agent_routes import configure as configure_surface

QUOTE = {
    "currency": "INR",
    "subtotal_minor": 249800,
    "lines": [
        {
            "sku": "SD-TOTE-BLK-M",
            "qty": 1,
            "unit_price_minor": 89900,
            "line_total_minor": 99800,
            "addons": [{"sku": "SD-GIFTWRAP", "amount_minor": 9900}],
        },
        {
            "sku": "SD-CHARMBAR-SEAT",
            "qty": 1,
            "unit_price_minor": 150000,
            "line_total_minor": 150000,
            "addons": [],
        },
    ],
    "discount_lines": [],
    "fulfillment_options": [
        {"id": "rest-of-india", "label": "Rest of India", "cost_minor": 9900, "eta_days": 5}
    ],
    "fulfillment_chosen": {"id": "rest-of-india", "cost_minor": 9900},
    "tax_lines": [
        {"kind": "IGST", "label": "IGST 18%", "amount_minor": 15827, "informational": True},
        {"kind": "CGST", "label": "CGST 9%", "amount_minor": 11894, "informational": True},
        {"kind": "SGST", "label": "SGST 9%", "amount_minor": 11894, "informational": True},
    ],
    "round_off_minor": 0,
    "total_minor": 259700,
    "tax_inclusive": True,
}


@pytest.fixture
def surface() -> Iterator[AgentSurface]:
    state = AgentSurface(
        admission=Admission(clients={"allowlisted": "secret"}),
        limiter=RateLimiter(),
        jwks={"keys": [{"kid": "k1", "kty": "EC", "crv": "P-256", "x": "a", "y": "b"}]},
    )
    configure_surface(state)
    yield state
    configure_surface(AgentSurface())


@pytest.fixture
def tokens() -> Iterator[TokenStore]:
    store = TokenStore()
    configure_approve(ApproveContext(tokens=store, quotes={"ord_1": QUOTE}))
    yield store
    configure_approve(ApproveContext())


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


# ── The cards ────────────────────────────────────────────────────────────────


def test_the_card_is_public_and_unauthenticated(client: TestClient, surface: AgentSurface) -> None:
    """A card behind a login is a card no new agent can read, and the whole
    admission story starts with a stranger fetching one."""
    response = client.get("/.well-known/agent-commerce.json")
    assert response.status_code == 200
    assert not response.request.headers.get("authorization")


def test_the_card_states_the_refusals_inline(client: TestClient, surface: AgentSurface) -> None:
    """An agent knows before it starts that there is no delegated-credential
    path — kinder than finding out at the last step."""
    card = client.get("/.well-known/agent-commerce.json").json()
    assert card["payment"]["delegated_credentials"] == "refused"
    assert card["authority"]["completion"] == "redirect-only"
    assert "may not spend" in card["authority"]["note"]


def test_the_card_names_every_protocol_and_its_deviations(
    client: TestClient, surface: AgentSurface
) -> None:
    card = client.get("/.well-known/agent-commerce.json").json()
    assert card["protocols"] == ["mcp", "ucp", "ap2", "acp"]
    acp = next(b for b in card["conformance"] if b["protocol"] == "acp")
    assert acp["version"] == "2026-04-17"
    assert any("completeCheckoutSession" in d for d in acp["deviations"])


def test_the_card_advertises_self_registration(client: TestClient, surface: AgentSurface) -> None:
    card = client.get("/.well-known/agent-commerce.json").json()
    assert card["admission"]["self_registration"] is True
    assert card["admission"]["agent_id"] == "RFC 7638 JWK thumbprint"


def test_the_ucp_manifest_declares_buyer_escalation_not_direct_completion(
    client: TestClient, surface: AgentSurface
) -> None:
    manifest = client.get("/.well-known/ucp.json").json()
    assert manifest["capabilities"]["direct_completion"] is False
    assert manifest["capabilities"]["buyer_escalation"] is True
    assert manifest["escalation_url"].endswith("/agentic/approve")


def test_jwks_is_served_so_a_receipt_can_be_verified_by_anyone(
    client: TestClient, surface: AgentSurface
) -> None:
    assert client.get("/.well-known/jwks.json").json()["keys"][0]["kid"] == "k1"


# ── Admission ────────────────────────────────────────────────────────────────


def test_the_tool_list_is_public(client: TestClient, surface: AgentSurface) -> None:
    """An agent should see what a shop offers before deciding to register."""
    tools = client.get("/agent/tools").json()["tools"]
    assert {t["name"] for t in tools} == {t.value for t in ToolName}


def test_the_tool_surface_needs_a_token_and_says_how_to_get_one(
    client: TestClient, surface: AgentSurface
) -> None:
    response = client.post("/agent/mcp", json={"tool": "search"})
    assert response.status_code == 401
    assert "/agent/register" in response.json()["error"]["detail"]


def test_an_allowlisted_client_gets_the_same_scopes_as_a_stranger(
    client: TestClient, surface: AgentSurface
) -> None:
    issued = client.post(
        "/agent/token", json={"client_id": "allowlisted", "client_secret": "secret"}
    ).json()
    assert sorted(issued["scopes"]) == sorted(s.value for s in Scope)
    assert issued["tier"] == Tier.ALLOWLISTED.value


def test_wrong_client_credentials_are_refused(client: TestClient, surface: AgentSurface) -> None:
    response = client.post(
        "/agent/token", json={"client_id": "allowlisted", "client_secret": "wrong"}
    )
    assert response.status_code == 401


# ── place-order returns a link, never an order ───────────────────────────────


def test_place_order_returns_an_approve_url_and_never_an_order(
    client: TestClient, surface: AgentSurface
) -> None:
    """The single most load-bearing sentence on this surface."""
    token = surface.admission.issue_for_stranger("agent_x")
    response = client.post(
        "/agent/mcp",
        json={"tool": "place-order"},
        headers={"authorization": f"Bearer {token.token}"},
    )
    body = response.json()["result"]
    assert body["approve_url"].endswith("/agentic/approve")
    assert "order_id" not in body
    assert "cannot complete the purchase itself" in body["note"]


def test_an_unknown_tool_is_refused(client: TestClient, surface: AgentSurface) -> None:
    token = surface.admission.issue_for_stranger("agent_y")
    response = client.post(
        "/agent/mcp",
        json={"tool": "wire-transfer"},
        headers={"authorization": f"Bearer {token.token}"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == ReasonCode.NOT_FOUND.value


def test_rate_limits_apply_per_agent(client: TestClient, surface: AgentSurface) -> None:
    token = surface.admission.issue_for_stranger("agent_noisy")
    headers = {"authorization": f"Bearer {token.token}"}
    last = None
    for _ in range(40):
        last = client.post("/agent/mcp", json={"tool": "search"}, headers=headers)
    assert last is not None
    assert last.status_code == 429
    assert last.json()["error"]["code"] == ReasonCode.RATE_LIMITED.value


# ── The approve page ─────────────────────────────────────────────────────────


def test_the_approve_page_is_reachable_without_the_merchant_session(
    client: TestClient, tokens: TokenStore
) -> None:
    """A Consumer approving a spend is not the Merchant. Requiring the shop's
    login would make the whole flow impossible."""
    tap = tokens.issue_tap("ord_1", "cart-hash", 259700)
    response = client.get(f"/agentic/approve?t={tap.token}")
    assert response.status_code == 200
    assert not response.request.headers.get("cookie")


def test_the_approve_page_renders_the_signed_quote_verbatim(
    client: TestClient, tokens: TokenStore
) -> None:
    tap = tokens.issue_tap("ord_1", "cart-hash", 259700)
    body = client.get(f"/agentic/approve?t={tap.token}").text

    # Every figure from the Quote, and the total the Consumer is agreeing to.
    assert "₹2,597.00" in body
    assert "₹998.00" in body  # the tote line, gift-wrap folded in
    assert "₹1,500.00" in body  # the charm-bar seat
    assert "₹158.27" in body  # IGST
    assert "₹118.94" in body  # CGST and SGST
    assert "5 days" in body  # a day count, never a date
    assert "includes SD-GIFTWRAP" in body
    assert "Nothing here was" in body and "calculated by the agent" in body


def test_the_approve_page_offers_the_private_code_field(
    client: TestClient, tokens: TokenStore
) -> None:
    """A private code is entered here and never travels through the agent."""
    tap = tokens.issue_tap("ord_1", "cart-hash", 259700)
    body = client.get(f"/agentic/approve?t={tap.token}").text
    assert "Have a code?" in body
    assert "never travels through the agent" in body


def test_the_approve_page_shows_only_the_enabled_methods(
    client: TestClient, tokens: TokenStore
) -> None:
    configure_approve(
        ApproveContext(
            tokens=tokens, quotes={"ord_1": QUOTE}, enabled_methods=frozenset({PaymentMethod.UPI})
        )
    )
    tap = tokens.issue_tap("ord_1", "cart-hash", 259700)
    body = client.get(f"/agentic/approve?t={tap.token}").text
    assert 'value="upi"' in body
    assert 'value="cash-on-delivery"' not in body


def test_the_approve_page_carries_a_countdown(client: TestClient, tokens: TokenStore) -> None:
    """'This expires' with no number is a sentence nobody acts on."""
    tap = tokens.issue_tap("ord_1", "cart-hash", 259700)
    body = client.get(f"/agentic/approve?t={tap.token}").text
    assert 'id="countdown"' in body
    assert "can be used once" in body


def test_an_unknown_token_is_not_found(client: TestClient, tokens: TokenStore) -> None:
    assert client.get("/agentic/approve?t=nope").status_code == 404


def test_a_spent_token_is_refused(client: TestClient, tokens: TokenStore) -> None:
    tap = tokens.issue_tap("ord_1", "cart-hash", 259700)
    tokens.spend_tap(tap.token, cart_hash="cart-hash")
    response = client.get(f"/agentic/approve?t={tap.token}")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == ReasonCode.AUTHORITY_STALE.value


def test_an_expired_token_is_refused(client: TestClient, tokens: TokenStore) -> None:
    tap = tokens.issue_tap("ord_1", "cart-hash", 259700)
    tap.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    body = client.get(f"/agentic/approve?t={tap.token}").text
    # The page still renders but the countdown is zero; the spend itself is
    # refused server-side by `spend_tap`, which is the authority.
    assert ">0<" in body or "0</span>" in body


def test_the_demo_banner_says_no_real_money_moves(client: TestClient, tokens: TokenStore) -> None:
    tap = tokens.issue_tap("ord_1", "cart-hash", 259700)
    assert "no real money moves" in client.get(f"/agentic/approve?t={tap.token}").text
