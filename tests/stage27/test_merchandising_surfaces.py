# tests/stage27/test_surfaces.py
# Stage 27.6: /merchant/merchandising (author/review/why-stat), the
# /merchandising/* ceremony routes, /web/suggestions, /chat rendering, and
# the MCP suggest_related tool — through the full app, as served.

from __future__ import annotations

from pathlib import Path

import openstore.core.database as _db_mod
import pytest
from conftest import CATALOG_YAML
from fastapi.testclient import TestClient
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
from openstore.core.database import get_session, init_database
from openstore.core.session import SESSION_COOKIE, csrf_token_for, mint_session
from openstore.server import create_app


def _settings(tmp_path: Path) -> Settings:
    cat = tmp_path / "catalog.yaml"
    cat.write_text(CATALOG_YAML)
    return Settings(
        merchant=MerchantConfig(name="Gelateria Milano", currency="INR"),
        razorpay=RazorpayConfig(key_id="rzp_test_x", key_secret="s"),
        discord=DiscordConfig(
            bot_token="token", buyer_trace_channel_id=1,
            merchant_trace_channel_id=2, money_trace_channel_id=3, alerts_channel_id=4,
        ),
        webauthn=WebAuthnConfig(rp_id="localhost", rp_name="T", origin="http://localhost:8000"),
        database=DatabaseConfig(url=f"sqlite:///{tmp_path}/stage27.db"),
        llm=LLMSettings(),
        campaign=CampaignSettings(),
        catalog_path=str(cat),
    )


@pytest.fixture()
def client(tmp_path):
    cfg = _settings(tmp_path)
    _db_mod._engine = None
    _db_mod._engine_url = None
    init_database(cfg)
    import openstore.surfaces.catalog as _cat

    _cat.CATALOG_CACHE = None
    _cat._CATALOG_BY_PATH.clear()
    app = create_app(cfg)
    c = TestClient(app, base_url="http://localhost:8000")
    c._openstore_config = cfg  # type: ignore[attr-defined]
    return c


def _authed(client: TestClient) -> str:
    cfg = client._openstore_config  # type: ignore[attr-defined]
    db = get_session(cfg)
    try:
        raw = mint_session(db, "merchant-1", "cred_x", None)
        db.commit()
    finally:
        db.close()
    client.cookies.set(SESSION_COOKIE, raw)
    return raw


def _author(client: TestClient, headers: dict, **kw) -> dict:
    body = {
        "action": "author",
        "kind": "CROSS_SELL",
        "title": "Pistachio with vanilla",
        "rationale": "merchant-authored",
        "trigger_skus": ["gelato_vanilla"],
        "suggested_sku": "gelato_pistachio",
    }
    body.update(kw)
    r = client.post("/merchant/merchandising", json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


class TestMerchandisingPage:
    def test_page_renders_stub_free(self, client):
        raw = _authed(client)
        _ = raw
        r = client.get("/merchant/merchandising")
        assert r.status_code == 200
        assert "Merchandising" in r.text
        assert "Stage 27" not in r.text
        assert "Goes well with" not in r.text  # chat phrasing stays on /chat

    def test_author_submits_for_approval(self, client):
        raw = _authed(client)
        out = _author(client, {"X-OpenStore-CSRF": csrf_token_for(raw)})
        assert out["ok"] is True and out["state"] == "PENDING_APPROVAL"
        r = client.get("/merchant/merchandising")
        assert "Pistachio with vanilla" in r.text
        assert "Why:" in r.text  # why-stat panel renders per rule

    def test_author_rejects_unknown_sku(self, client):
        raw = _authed(client)
        r = client.post(
            "/merchant/merchandising",
            json={"action": "author", "kind": "CROSS_SELL", "title": "T",
                  "trigger_skus": ["gelato_vanilla"], "suggested_sku": "nope"},
            headers={"X-OpenStore-CSRF": csrf_token_for(raw)},
        )
        assert r.status_code == 422
        assert r.json()["detail"]["reason_code"] == "catalog.sku_not_found"

    def test_author_rejects_bad_kind(self, client):
        raw = _authed(client)
        r = client.post(
            "/merchant/merchandising",
            json={"action": "author", "kind": "SLEDGEHAMMER", "title": "T",
                  "trigger_skus": ["gelato_vanilla"], "suggested_sku": "cone_waffle"},
            headers={"X-OpenStore-CSRF": csrf_token_for(raw)},
        )
        assert r.status_code == 422

    def test_approve_route_rejects_out_of_order(self, client):
        """Approve on a DRAFT (never submitted) fails closed with the
        merchandising closed set — the ceremony routes are wired."""
        raw = _authed(client)
        out = _author(client, {"X-OpenStore-CSRF": csrf_token_for(raw)})
        # Author auto-submits; craft a real DRAFT via the core path instead.
        cfg = client._openstore_config  # type: ignore[attr-defined]
        from openstore.core.merchandising import create_rule
        from openstore.models import MerchandisingKind

        db = get_session(cfg)
        try:
            draft = create_rule(
                db, cfg, "gelateria-milano", MerchandisingKind.CROSS_SELL,
                "D", "R", ["gelato_vanilla"], "cone_waffle",
            )
            db.commit()
            draft_id = draft.id
        finally:
            db.close()
        _ = out
        r = client.post(
            f"/merchandising/{draft_id}/approve",
            json={"approver_credential_id": "c", "client_data_json": "c",
                  "authenticator_data": "a", "signature": "s", "challenge": "h"},
            headers={"X-OpenStore-CSRF": csrf_token_for(raw),
                     "X-Operator-Id": "merchant-1"},
        )
        assert r.status_code == 409
        assert r.json()["detail"]["reason_code"] == "merchandising.invalid_state_transition"

    def test_approve_route_needs_assertion(self, client):
        raw = _authed(client)
        out = _author(client, {"X-OpenStore-CSRF": csrf_token_for(raw)})
        r = client.post(
            f"/merchandising/{out['rule_id']}/approve",
            json={"approver_credential_id": "", "client_data_json": "",
                  "authenticator_data": "", "signature": "", "challenge": ""},
            headers={"X-OpenStore-CSRF": csrf_token_for(raw),
                     "X-Operator-Id": "merchant-1"},
        )
        assert r.status_code == 401
        assert r.json()["detail"]["reason_code"] == "merchandising.no_webauthn_approval"


class TestWebSuggestions:
    def test_suggestions_follow_rules(self, client):
        raw = _authed(client)
        _author(client, {"X-OpenStore-CSRF": csrf_token_for(raw)})
        # Author auto-submits to PENDING — flip to ACTIVE directly: the
        # endpoint reads selection state, and the ceremony has its own test.
        cfg = client._openstore_config  # type: ignore[attr-defined]
        from openstore.models import CampaignState, MerchandisingRule
        from sqlmodel import select

        db = get_session(cfg)
        try:
            for row in db.exec(select(MerchandisingRule)).all():
                row.state = CampaignState.ACTIVE
                db.add(row)
            db.commit()
        finally:
            db.close()
        r = client.get("/web/suggestions", params={"skus": "gelato_vanilla"})
        assert r.status_code == 200
        skus = [s["sku"] for s in r.json()["suggestions"]]
        assert skus[0] == "gelato_pistachio"
        assert "cone_waffle" in skus

    def test_chat_renders_suggestion_surfaces(self, client):
        r = client.get("/chat")
        assert r.status_code == 200
        assert "Goes well with" in r.text
        assert "Upgrade to" in r.text


class TestMCPSuggestRelated:
    def _call(self, client, arguments: dict) -> dict:
        r = client.post(
            "/agent/mcp",
            json={"id": 1, "jsonrpc": "2.0", "method": "tools/call",
                  "params": {"name": "suggest_related", "arguments": arguments}},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["result"]["isError"] is False
        import json as _json

        return _json.loads(body["result"]["content"][0]["text"])

    def test_tool_answers_suggestions(self, client):
        data = self._call(client, {"skus": ["gelato_vanilla"]})
        assert data["success"] is True
        assert [s["sku"] for s in data["data"]["suggestions"]] == ["cone_waffle"]

    def test_empty_cart_answers_empty(self, client):
        data = self._call(client, {"skus": []})
        assert data["success"] is True
        assert data["data"]["suggestions"] == []


class TestUpgradeEmbed:
    def test_upgrade_embed_carries_honest_delta(self):
        from openstore.agents.buyer_agent import build_upgrade_embed

        assert build_upgrade_embed([]) is None
        assert build_upgrade_embed([{"sku": "a", "name": "A", "unit_minor": 1}]) is None
        embed = build_upgrade_embed([{
            "sku": "topping_gold", "name": "Gold Topping", "unit_minor": 50000,
            "kind": "UPGRADE", "price_delta_minor": 35000,
        }])
        assert embed is not None and embed["title"] == "Upgrade to"
        assert "Gold Topping" in embed["description"]
        assert "350.00" in embed["description"]

    def test_cross_sell_title_renamed(self):
        from openstore.agents.buyer_agent import build_upsell_nudge_embed

        embed = build_upsell_nudge_embed(
            [{"sku": "cone_waffle", "name": "Waffle Cone", "unit_minor": 3000}]
        )
        assert embed is not None and embed["title"] == "Goes well with"
