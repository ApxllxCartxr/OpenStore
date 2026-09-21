"""The cards the sidecar serves are the cards the chat is tested against.

`tests/GOLDEN/card/` is read by two suites in two languages: this one, which
asserts the live routes still produce it, and `demo/buyer-chat/tests/
real-card.test.ts`, which feeds it to the fetcher that has to read it. Neither
side can drift without the other going red.

Regenerate with `uv run scripts/make_card_golden.py` — and when that changes a
byte, the chat's suite is the thing to run before believing it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openstore.sidecar.app import app
from openstore.sidecar.protocols.agent_routes import AgentSurface
from openstore.sidecar.protocols.agent_routes import configure as configure_surface

GOLDEN = Path(__file__).parent / "GOLDEN" / "card"
DOMAIN = "spoiledduckie.localhost"
ORIGIN = f"http://{DOMAIN}"


def golden(name: str) -> dict:
    return json.loads((GOLDEN / name).read_text(encoding="utf-8"))


@pytest.fixture
def demo_surface() -> Iterator[None]:
    """The demo's configuration: a plain-http origin that is not `https://<domain>`."""
    configure_surface(
        AgentSurface(
            merchant_domain=DOMAIN,
            merchant_name="SpoiledDuckie",
            public_origin=ORIGIN,
            jwks=golden("jwks.json"),
        )
    )
    yield
    configure_surface(AgentSurface())


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def test_the_card_matches_the_golden(client: TestClient, demo_surface: None) -> None:
    assert client.get("/.well-known/agent-commerce.json").json() == golden("agent-commerce.json")


def test_the_manifest_matches_the_golden(client: TestClient, demo_surface: None) -> None:
    assert client.get("/.well-known/ucp.json").json() == golden("ucp.json")


def test_the_jwks_matches_the_golden(client: TestClient, demo_surface: None) -> None:
    assert client.get("/.well-known/jwks.json").json() == golden("jwks.json")


def test_the_card_advertises_the_configured_origin_not_an_assumed_https_one(
    client: TestClient, demo_surface: None
) -> None:
    """A card that assumes `https://<domain>` on a plain-http deploy hands the
    agent seven endpoints that do not answer — including the one it needs to
    fetch the keys that make anything else checkable."""
    card = client.get("/.well-known/agent-commerce.json").json()
    assert all(url.startswith(f"{ORIGIN}/") for url in card["endpoints"].values()), card[
        "endpoints"
    ]
    assert card["endpoints"]["jwks"] == f"{ORIGIN}/.well-known/jwks.json"


def test_an_unset_origin_still_means_https(client: TestClient) -> None:
    """The default is right for every real install; only the demo overrides it."""
    configure_surface(AgentSurface(merchant_domain=DOMAIN, merchant_name="SpoiledDuckie"))
    try:
        card = client.get("/.well-known/agent-commerce.json").json()
        assert card["endpoints"]["mcp"] == f"https://{DOMAIN}/agent/mcp"
    finally:
        configure_surface(AgentSurface())


def test_the_card_carries_no_keys_inline(client: TestClient, demo_surface: None) -> None:
    """The card names where the keys live; it does not carry them (SPEC §V1).
    The chat spent its whole life expecting the opposite."""
    card = client.get("/.well-known/agent-commerce.json").json()
    assert "jwks" not in card
    assert card["endpoints"]["jwks"]
