# tests/stage21/test_conformance_fixtures.py
# Stage 21 (Q-041 / DECISION-041) — third-party-verifiable conformance.
#
# Each file in tests/GOLDEN/conformance/ pins one wire interaction
# byte-for-byte (canonical JSON): the exact request a stranger's agent sends
# and the exact bytes this sidecar answers. Replay any of them with curl
# against your own origin; only the origin-derived URLs change (see
# test_manifest_urls_derive_from_request_origin).
#
# Fixtures cover: MCP 2025-06-18 initialize + tools/list (all 23 tools),
# search_catalog / lookup_catalog / get_product in UCP {meta, catalog} shape
# (hit, partial-miss, legacy shape), suggest_related, unknown-tool isError,
# legacy-envelope -32600 rejection, missing-catalog -32602, and both
# discovery manifests.
# Seeded catalog is fixed (2 gelato SKUs) so catalog bytes are stable.
# Golden discipline applies: a fixture break means the wire changed — review
# the diff as a spec change, never "fix" the vector to match.

from __future__ import annotations

import json
from pathlib import Path

import pytest
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
from openstore.core.database import init_database
from openstore.server import create_app

GOLDEN = Path(__file__).resolve().parents[1] / "GOLDEN" / "conformance"

FIXTURES = [
    "mcp_initialize",
    "mcp_tools_list",
    "mcp_search_catalog_ucp",
    "mcp_lookup_catalog_hit",
    "mcp_lookup_catalog_partial",
    "mcp_get_product_legacy",
    "mcp_get_product_ucp",
    "mcp_suggest_related",
    "mcp_unknown_tool",
    "mcp_lookup_missing_catalog",
    "mcp_legacy_shape_rejected",
    "ucp_manifest",
    "agent_commerce_manifest",
]

CATALOG_YAML = """
items:
  - sku: GEL-VAN-500
    name: Madagascar Vanilla 500ml
    unit_minor: 21000
    tags: [vegan, dairy-free]
    description: Slow-churned, cashew-base vanilla.
  - sku: GEL-HAZ-500
    name: Roasted Hazelnut 500ml
    unit_minor: 23000
    tags: [vegan]
    description: Piedmont hazelnuts, dark roast.
"""


def _canon(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


@pytest.fixture()
def client(tmp_path):
    import openstore.surfaces.catalog as catalog_mod

    catalog_mod.CATALOG_CACHE = None
    catalog_mod._CATALOG_BY_PATH.clear()
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(CATALOG_YAML)
    config = Settings(
        merchant=MerchantConfig(name="Gelateria Milano", currency="INR"),
        razorpay=RazorpayConfig(key_id="rzp_test_xxxxxxxx", key_secret="test"),
        discord=DiscordConfig(
            bot_token="t",
            buyer_trace_channel_id=1,
            merchant_trace_channel_id=2,
            money_trace_channel_id=3,
            alerts_channel_id=4,
        ),
        webauthn=WebAuthnConfig(
            rp_id="localhost", rp_name="OpenStore", origin="http://localhost:8000"
        ),
        database=DatabaseConfig(url="sqlite://"),
        llm=LLMSettings(),
        campaign=CampaignSettings(),
        catalog_path=str(catalog),
    )
    import openstore.core.database as db_mod

    db_mod._engine = None
    init_database(config)
    return TestClient(create_app(config))


def _load(name: str) -> dict:
    return json.loads((GOLDEN / f"{name}.json").read_text(encoding="utf-8"))


class TestConformanceGoldens:
    @pytest.mark.parametrize("name", FIXTURES)
    def test_fixture_replays_byte_exact(self, client, name):
        doc = _load(name)
        req = doc["request"]
        if req["method"] == "GET":
            response = client.get(req["path"])
        else:
            response = client.post(req["path"], json=req["body"])
        assert response.status_code == doc["expect_status"], name
        assert _canon(response.json()) == _canon(doc["expect_body"]), name

    def test_all_fixtures_present(self):
        on_disk = {p.stem for p in GOLDEN.glob("*.json")}
        assert set(FIXTURES) == on_disk

    def test_manifest_urls_derive_from_request_origin(self, client):
        """The goldens pin http://testserver; the portable claim is that no
        URL is hardcoded — every absolute URL in both discovery documents
        starts with the request origin."""
        origin = "http://testserver"
        found: list[str] = []

        def walk(value: object) -> None:
            if isinstance(value, dict):
                for v in value.values():
                    walk(v)
            elif isinstance(value, list):
                for v in value:
                    walk(v)
            elif isinstance(value, str) and value.startswith("http"):
                found.append(value)

        for name in ("ucp_manifest", "agent_commerce_manifest"):
            walk(client.get(_load(name)["request"]["path"]).json())
        assert found, "expected absolute URLs in the discovery documents"
        assert all(u.startswith(origin) for u in found), [
            u for u in found if not u.startswith(origin)
        ]
