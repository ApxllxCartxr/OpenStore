# tests/stage12/test_enrollment_aggregator.py
# S12 UX polish: surfaces/buyer_studio.py aggregates a federated cart's N
# per-merchant signing links into one hosted page (BuyerAgent.create_enrollment_group).
# This carries no signing authority of its own (DECISION-022: no signing
# hub) — these tests pin that the page only ever (a) echoes back the static
# entries BuyerAgent recorded and (b) reports signed/not via an independent
# resolve_policy call per merchant, same trust rule as buyer_internal.py.

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openstore.agents.buyer_agent import BuyerAgent
from openstore.config import (
    CampaignSettings,
    DatabaseConfig,
    DiscordConfig,
    LLMSettings,
    MerchantConfig,
    RazorpayConfig,
    Settings,
    WebAuthnConfig,
)
from openstore.surfaces.buyer_studio import create_buyer_studio_router


def _config() -> Settings:
    return Settings(
        merchant=MerchantConfig(name="Test"),
        razorpay=RazorpayConfig(key_id="k", key_secret="s"),
        discord=DiscordConfig(
            bot_token="token",
            buyer_trace_channel_id=1,
            merchant_trace_channel_id=2,
            money_trace_channel_id=3,
            alerts_channel_id=4,
        ),
        webauthn=WebAuthnConfig(rp_id="l", rp_name="x", origin="http://l"),
        database=DatabaseConfig(url="sqlite://"),
        llm=LLMSettings(),
        campaign=CampaignSettings(),
    )


class _FakeHttpClient:
    def __init__(self, response: dict[str, Any]):
        self.response = response
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(
        self, tool_name: str, arguments: dict[str, Any], *, require_auth: bool
    ) -> dict[str, Any]:
        self.calls.append((tool_name, arguments))
        return self.response


class _FakeFederatingMCPClient:
    def __init__(self, clients: dict[str, _FakeHttpClient]):
        self._clients = clients

    def client_for(self, merchant_id: str) -> _FakeHttpClient:
        return self._clients[merchant_id]


def _build_app(agent: BuyerAgent) -> TestClient:
    config = agent.config
    app = FastAPI()
    app.include_router(create_buyer_studio_router(config, agent))
    return TestClient(app)


def test_unknown_group_id_404s() -> None:
    agent = BuyerAgent(_config(), _FakeFederatingMCPClient({}))
    client = _build_app(agent)

    assert client.get("/enroll/does-not-exist").status_code == 404
    assert client.get("/enroll/does-not-exist/status").status_code == 404


def test_page_lists_every_entry_and_embeds_its_own_studio_link() -> None:
    agent = BuyerAgent(_config(), _FakeFederatingMCPClient({}))
    group_id = agent.create_enrollment_group(
        "discord:u1",
        [
            {
                "merchant_id": "store-a",
                "merchant_name": "Store A",
                "sign_url": "https://a.example/intent/studio?token=t1",
            },
            {
                "merchant_id": "store-b",
                "merchant_name": "Store B",
                "sign_url": "https://b.example/intent/studio?token=t2",
            },
        ],
    )
    client = _build_app(agent)

    resp = client.get(f"/enroll/{group_id}")
    assert resp.status_code == 200
    assert "https://a.example/intent/studio?token=t1" in resp.text
    assert "https://b.example/intent/studio?token=t2" in resp.text
    assert "Store A" in resp.text
    assert "Store B" in resp.text


def test_status_reports_per_merchant_signed_state_via_independent_resolve_policy() -> None:
    """The page must never trust anything cached at group-creation time —
    signed/not comes from calling resolve_policy at each merchant fresh,
    every poll."""
    signed_client = _FakeHttpClient({"success": True, "data": {"policy_id": "pol_1"}})
    unsigned_client = _FakeHttpClient(
        {"success": False, "error": {"reason_code": "authority.policy_unsigned"}}
    )
    mcp = _FakeFederatingMCPClient({"store-a": signed_client, "store-b": unsigned_client})
    agent = BuyerAgent(_config(), mcp)
    group_id = agent.create_enrollment_group(
        "discord:u1",
        [
            {
                "merchant_id": "store-a",
                "merchant_name": "Store A",
                "sign_url": "https://a.example/s",
            },
            {
                "merchant_id": "store-b",
                "merchant_name": "Store B",
                "sign_url": "https://b.example/s",
            },
        ],
    )
    client = _build_app(agent)

    resp = client.get(f"/enroll/{group_id}/status")
    assert resp.status_code == 200
    assert resp.json() == {"signed": {"store-a": True, "store-b": False}}
    assert signed_client.calls == [("resolve_policy", {"user_id": "discord:u1"})]
    assert unsigned_client.calls == [("resolve_policy", {"user_id": "discord:u1"})]


@pytest.mark.asyncio
async def test_federated_cart_surfaces_one_aggregator_link_when_resume_url_is_wired() -> None:
    """_submit_federated_cart's own wiring: with resume_url set (as
    buyer_cli.py always does), two unenrolled merchants collapse into one
    /enroll/<group_id> link instead of two raw per-merchant links."""

    def _origin_client(merchant_id: str, name: str) -> Any:
        client = _FakeHttpClient(
            {"success": False, "error": {"reason_code": "authority.policy_unsigned"}}
        )

        async def call(
            tool_name: str, arguments: dict[str, Any], *, require_auth: bool = True
        ) -> dict[str, Any]:
            client.calls.append((tool_name, arguments))
            if tool_name == "resolve_policy":
                return client.response
            if tool_name == "create_policy_handoff":
                return {"success": True, "data": {"token": f"tok-{merchant_id}"}}
            raise AssertionError(tool_name)

        client.call = call  # type: ignore[method-assign]
        client.merchant = SimpleNamespace(
            base_url=f"https://{merchant_id}.example",
            merchant_id=merchant_id,
            name=name,
        )
        return client

    client_a = _origin_client("store-a", "Store A")
    client_b = _origin_client("store-b", "Store B")
    mcp = _FakeFederatingMCPClient({"store-a": client_a, "store-b": client_b})
    agent = BuyerAgent(_config(), mcp, resume_url="http://127.0.0.1:8765/internal/signing-complete")

    cart = [
        {
            "merchant_id": "store-a",
            "sku": "sku-a",
            "qty": 1,
            "unit_minor": 500,
            "tags": [],
            "name": "sku-a",
        },
        {
            "merchant_id": "store-b",
            "sku": "sku-b",
            "qty": 1,
            "unit_minor": 300,
            "tags": [],
            "name": "sku-b",
        },
    ]
    result = await agent._submit_federated_cart(
        cart, "trace-agg", "discord", "u1", "c1", None, "goal text"
    )

    assert result["awaiting_reply"] is True
    assert result["question"].count("http") == 1  # one link, not two
    assert "/enroll/" in result["question"]
    assert "intent/studio" not in result["question"]

    # The link's group_id resolves to both merchants' real sign_urls.
    group_id = result["question"].rsplit("/enroll/", 1)[1]
    group = agent.get_enrollment_group(group_id)
    assert group is not None
    urls = {e["merchant_id"]: e["sign_url"] for e in group["entries"]}
    assert urls == {
        "store-a": "https://store-a.example/intent/studio?token=tok-store-a",
        "store-b": "https://store-b.example/intent/studio?token=tok-store-b",
    }
