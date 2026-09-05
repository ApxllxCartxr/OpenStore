# tests/stage12/test_sku_namespacing.py
# S12: all_search_results is keyed by "merchant_id::sku", not bare sku — once
# a buyer's search spans more than one merchant, two stores selling the same
# sku (e.g. "gelato_vanilla") must not collide and silently validate a
# selection against the WRONG store's price/tags.

from __future__ import annotations

import pytest
from openstore.agents.buyer_graph import BuyerGraph, BuyerPlanError
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


@pytest.fixture()
def config() -> Settings:
    return Settings(
        merchant=MerchantConfig(name="Test Merchant", currency="INR"),
        razorpay=RazorpayConfig(key_id="rzp_test_xxx", key_secret="s"),
        discord=DiscordConfig(
            bot_token="token",
            buyer_trace_channel_id=1,
            merchant_trace_channel_id=2,
            money_trace_channel_id=3,
            alerts_channel_id=4,
        ),
        webauthn=WebAuthnConfig(rp_id="localhost", rp_name="OpenStore", origin="http://localhost"),
        database=DatabaseConfig(url="sqlite://"),
        llm=LLMSettings(),
        campaign=CampaignSettings(),
    )


class _TwoMerchantSameSkuMCPClient:
    """Simulates a federating client that already stamps its own
    merchant_id per item (setdefault must not override it) — two DIFFERENT
    merchants both sell "gelato_vanilla", at different prices."""

    async def call(self, tool_name: str, arguments: dict) -> dict:
        return {"success": False, "data": {}, "error": {"reason_code": "fake.noop"}}

    async def search_products(
        self, query: str, tags: list[str] | None = None, limit: int = 10
    ) -> dict:
        return {
            "success": True,
            "data": {
                "items": [
                    {
                        "sku": "gelato_vanilla",
                        "merchant_id": "store-a",
                        "unit_minor": 15000,
                        "tags": ["vegan"],
                    },
                    {
                        "sku": "gelato_vanilla",
                        "merchant_id": "store-b",
                        "unit_minor": 30000,
                        "tags": ["premium"],
                    },
                ]
            },
            "error": None,
        }

    async def get_order(self, checkout_id: str) -> dict:
        return {"success": False, "data": {}, "error": {"reason_code": "fake.noop"}}


def _register_queued_provider(monkeypatch, name: str, responses: list) -> list:
    import json as _json

    from openstore.agents.llm import DummyProvider, register_provider

    captured: list = []
    queue = list(responses)

    class _Provider(DummyProvider):
        def chat(self, messages, **kwargs):
            captured.append(messages)
            response = queue.pop(0)
            return response if isinstance(response, str) else _json.dumps(response)

    register_provider(name, _Provider)
    monkeypatch.setenv("LLM_PROVIDER", name)
    return captured


class TestSkuNamespacing:
    async def test_same_sku_different_merchants_both_survive_the_merge(self, config, monkeypatch):
        """Two search results sharing a bare sku, from different merchants,
        must both remain reachable in all_search_results — the composite key
        (merchant_id::sku) must not let one clobber the other."""
        from openstore.agents.buyer_graph import _rebuild_search_results_from_messages

        _register_queued_provider(
            monkeypatch,
            "test_sku_ns_survive",
            [
                {"action": "search", "query": "vanilla"},
                {"action": "ask", "message": "which store would you like?"},
            ],
        )
        graph = BuyerGraph(config, _TwoMerchantSameSkuMCPClient())
        result = await graph.converse(
            [{"role": "user", "content": "vanilla gelato"}], "p_001", "trace_ns_survive"
        )
        assert result["awaiting_reply"] is True

        merged = _rebuild_search_results_from_messages(result["messages"])
        assert merged["store-a::gelato_vanilla"]["unit_minor"] == 15000
        assert merged["store-b::gelato_vanilla"]["unit_minor"] == 30000
        assert len(merged) == 2

    async def test_validate_selection_picks_the_correct_stores_price(self, config, monkeypatch):
        """A selection naming the SAME sku but a DIFFERENT merchant_id must
        resolve to that store's own unit_minor/tags — not the other store's,
        and not whatever price the LLM's own JSON claimed."""
        _register_queued_provider(
            monkeypatch,
            "test_sku_ns_price",
            [
                {"action": "search", "query": "vanilla"},
                {
                    "action": "answer",
                    "selections": [{"sku": "gelato_vanilla", "merchant_id": "store-b", "qty": 1}],
                },
            ],
        )
        graph = BuyerGraph(config, _TwoMerchantSameSkuMCPClient())
        result = await graph.converse(
            [{"role": "user", "content": "vanilla gelato"}], "p_001", "trace_ns_price"
        )
        assert result["cart"] == [
            {
                "sku": "gelato_vanilla",
                "merchant_id": "store-b",
                "qty": 1,
                "unit_minor": 30000,
                "tags": ["premium"],
                "name": "gelato_vanilla",
            }
        ]

    async def test_selection_with_unseen_merchant_id_is_hallucinated(self, config, monkeypatch):
        """A sku that IS real (came from a search) but paired with a
        merchant_id that never appeared in any search result must be
        rejected as hallucinated — the LLM can't smuggle a valid sku under a
        store it never actually saw it sold by."""
        _register_queued_provider(
            monkeypatch,
            "test_sku_ns_hallucinated",
            [
                {"action": "search", "query": "vanilla"},
                {
                    "action": "answer",
                    "selections": [
                        {"sku": "gelato_vanilla", "merchant_id": "store-z-unseen", "qty": 1}
                    ],
                },
            ],
        )
        graph = BuyerGraph(config, _TwoMerchantSameSkuMCPClient())
        with pytest.raises(BuyerPlanError, match="hallucinated_sku"):
            await graph.converse(
                [{"role": "user", "content": "vanilla gelato"}], "p_001", "trace_ns_hallucinated"
            )

    async def test_selection_missing_merchant_id_fails_loud(self, config, monkeypatch):
        """R0.5: a missing merchant_id on the LLM's selection must raise, not
        silently fall back to some default merchant."""
        _register_queued_provider(
            monkeypatch,
            "test_sku_ns_missing_merchant",
            [
                {"action": "search", "query": "vanilla"},
                {"action": "answer", "selections": [{"sku": "gelato_vanilla", "qty": 1}]},
            ],
        )
        graph = BuyerGraph(config, _TwoMerchantSameSkuMCPClient())
        with pytest.raises(BuyerPlanError, match="malformed_selection"):
            await graph.converse(
                [{"role": "user", "content": "vanilla gelato"}], "p_001", "trace_ns_missing"
            )


class _PrestampedMCPClient:
    """A federating client: every item already carries its own merchant_id."""

    async def search_products(self, query, tags=None, limit=20):
        return {
            "success": True,
            "data": {
                "items": [
                    {
                        "sku": "gelato_vanilla",
                        "name": "Vanilla Gelato",
                        "unit_minor": 15000,
                        "tags": [],
                        "merchant_id": "gelateria-milano",
                    },
                    {
                        "sku": "chai_masala",
                        "name": "Masala Chai",
                        "unit_minor": 6000,
                        "tags": [],
                        "merchant_id": "chai-house",
                    },
                ]
            },
        }


async def test_revision_turn_keeps_pending_cart_provenance(config, monkeypatch):
    """Regression (live Discord failure): a pending cart holds biscuit_parle
    from an earlier turn whose search message is no longer in the persisted
    transcript. The buyer says "I also want samosa" — the model searches
    samosa and answers with the COMPLETE revised set, as the prompt
    instructs. The old line must validate against the cart_ready marker
    instead of raising hallucinated_sku."""
    import json as _json

    _register_queued_provider(
        monkeypatch,
        "test_sku_ns_revision",
        [
            {"action": "search", "query": "samosa"},
            {
                "action": "answer",
                "selections": [
                    {"sku": "biscuit_parle", "merchant_id": "chai-house", "qty": 1},
                    {"sku": "samosa_aloo", "merchant_id": "chai-house", "qty": 1},
                ],
            },
        ],
    )

    class _SamosaOnlyMCPClient:
        async def search_products(self, query, tags=None, limit=20):
            return {
                "success": True,
                "data": {
                    "items": [
                        {
                            "sku": "samosa_aloo",
                            "name": "Aloo Samosa",
                            "unit_minor": 15000,
                            "tags": ["snack"],
                            "merchant_id": "chai-house",
                        }
                    ]
                },
            }

        async def list_campaigns(self):
            return {"success": True, "data": {"campaigns": []}}

    pending_cart = [
        {
            "sku": "biscuit_parle",
            "merchant_id": "chai-house",
            "qty": 1,
            "unit_minor": 2000,
            "tags": ["snack"],
            "name": "Parle-G Biscuits",
        }
    ]
    messages = [
        {"role": "user", "content": "a biscuit please"},
        {
            "role": "user",
            "content": _json.dumps({"tool_result": "cart_ready", "cart": pending_cart}),
        },
        {"role": "user", "content": "I also want samosa"},
    ]
    graph = BuyerGraph(config, _SamosaOnlyMCPClient())
    result = await graph.converse(messages, "p_001", "trace_ns_revision")
    assert result["awaiting_reply"] is True
    skus = sorted(line["sku"] for line in result["cart"])
    assert skus == ["biscuit_parle", "samosa_aloo"]
    biscuit = next(line for line in result["cart"] if line["sku"] == "biscuit_parle")
    assert biscuit["unit_minor"] == 2000  # server-stamped, not LLM-supplied


async def test_search_does_not_need_a_single_merchant_config_when_items_are_stamped():
    """Regression: the buyer process runs on a BuyerSettings, which has
    `merchants` (plural) and NO `merchant`. Resolving the single-merchant
    fallback eagerly — as dict.setdefault's argument would — raised
    AttributeError on every federated search, even though every item already
    carried a merchant_id and the fallback was never used.
    """
    from openstore.agents.buyer_graph import _run_search

    class _BuyerLikeConfig:
        """Stands in for BuyerSettings: deliberately has no `.merchant`."""

        merchants: list[str] = []

    state = {
        "messages": [{"role": "assistant", "content": '{"action": "search", "query": "anything"}'}],
        "all_search_results": {},
        "tool_calls_used": 0,
    }
    out = await _run_search(state, mcp=_PrestampedMCPClient(), config=_BuyerLikeConfig())
    assert sorted(out["all_search_results"]) == [
        "chai-house::chai_masala",
        "gelateria-milano::gelato_vanilla",
    ]
