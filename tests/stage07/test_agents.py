# tests/stage07/test_agents.py
# Stage 7 — Agents: buyer, merchant, negotiation, amendment, narrator

from __future__ import annotations

import pytest
from openstore.agents.buyer_agent import (
    BuyerAgent,
    build_shop_result_embed,
    compute_cart_hash,
    render_shop_result,
)
from openstore.agents.llm import AnthropicProvider, DummyProvider, LLMProvider
from openstore.agents.merchant_agent import MerchantAgent, NegotiationMessage
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


class FakeMCPClient:
    """Minimal stand-in for InProcessMCPClient in unit tests (no DB, no OAuth)."""

    async def call(self, tool_name: str, arguments: dict) -> dict:
        return {"success": False, "data": {}, "error": {"reason_code": "fake.noop"}}

    async def search_products(
        self, query: str, tags: list[str] | None = None, limit: int = 10
    ) -> dict:
        return {"success": True, "data": {"items": []}, "error": None}

    async def get_order(self, checkout_id: str) -> dict:
        return {"success": False, "data": {}, "error": {"reason_code": "fake.noop"}}


@pytest.fixture()
def mcp_client() -> FakeMCPClient:
    return FakeMCPClient()


class _MultiItemMCPClient(FakeMCPClient):
    """search_products returns a multi-item fixture so the LLM tool-calling
    loop's selection (and the deterministic copy-from-search-results, not
    from LLM output) can be exercised end-to-end."""

    async def search_products(
        self, query: str, tags: list[str] | None = None, limit: int = 10
    ) -> dict:
        return {
            "success": True,
            "data": {
                "items": [
                    {"sku": "gelato_vanilla", "unit_minor": 15000, "tags": ["vegan"]},
                    {"sku": "gelato_pistachio", "unit_minor": 18000, "tags": ["pistachio"]},
                    {"sku": "gelato_cream", "unit_minor": 16000, "tags": ["dairy"]},
                ]
            },
            "error": None,
        }


def _register_queued_provider(monkeypatch, name: str, responses: list) -> list:
    """Registers a provider that returns each of `responses` in order, one
    per chat() call (for the multi-step agent_step<->run_search loop, unlike
    _register_plan_provider's single fixed response). Returns the list of
    captured `messages` args, one per call, for assertions on what the graph
    actually sent (e.g. checking a search tool result reached the LLM)."""
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


class TestBuyerGraph:
    """Direct BuyerGraph tests — the tool-calling loop (agent_step <->
    run_search, "ask"/"answer" termination) is exercised here rather than
    through BuyerAgent.start_shop(), matching how MerchantAgent.negotiate()
    is tested directly rather than through the full shop() pipeline."""

    async def test_plan_selects_subset_with_fields_from_search_results(self, config, monkeypatch):
        from openstore.agents.buyer_graph import BuyerGraph

        _register_queued_provider(
            monkeypatch,
            "test_graph_subset",
            [
                {"action": "search", "query": "pistachio"},
                {
                    "action": "answer",
                    # LLM-supplied unit_minor/tags below must be IGNORED — the
                    # cart must be built from the search fixture's values, not these.
                    "selections": [
                        {"sku": "gelato_pistachio", "merchant_id": "test-merchant", "qty": 2}
                    ],
                },
            ],
        )
        graph = BuyerGraph(config, _MultiItemMCPClient())
        result = await graph.converse(
            [{"role": "user", "content": "pistachio gelato"}], "p_001", "trace_002"
        )
        assert result["cart"] == [
            {
                "sku": "gelato_pistachio",
                "merchant_id": "test-merchant",
                "qty": 2,
                "unit_minor": 18000,
                "tags": ["pistachio"],
                "name": "gelato_pistachio",
            }
        ]

    async def test_plan_accumulates_results_across_multiple_searches(self, config, monkeypatch):
        """The classic "no vanilla, but chocolate is available" case: a first
        search comes up empty, a second search (different term) finds
        something, and the final answer can still select from EITHER
        search's results (all_search_results is a union, not overwritten)."""
        from openstore.agents.buyer_graph import BuyerGraph

        class _EmptyThenGelatoMCPClient(_MultiItemMCPClient):
            async def search_products(self, query, tags=None, limit=20):
                if query == "vanilla":
                    return {"success": True, "data": {"items": []}, "error": None}
                return await super().search_products(query, tags=tags, limit=limit)

        _register_queued_provider(
            monkeypatch,
            "test_graph_multi_search",
            [
                {"action": "search", "query": "vanilla"},
                {"action": "search", "query": "gelato"},
                {
                    "action": "answer",
                    "selections": [
                        {"sku": "gelato_cream", "merchant_id": "test-merchant", "qty": 1}
                    ],
                },
            ],
        )
        graph = BuyerGraph(config, _EmptyThenGelatoMCPClient())
        result = await graph.converse(
            [{"role": "user", "content": "vanilla gelato please"}], "p_001", "trace_003"
        )
        assert result["cart"] == [
            {
                "sku": "gelato_cream",
                "merchant_id": "test-merchant",
                "qty": 1,
                "unit_minor": 16000,
                "tags": ["dairy"],
                "name": "gelato_cream",
            }
        ]

    async def test_search_query_list_covers_multiple_items_in_one_tool_call(
        self, config, monkeypatch
    ):
        """S17 regression (found live): a buyer naming 3 distinct items
        ("vanilla, pistachio and a waffle cone") made the LLM issue one
        search action PER item, exhausting MAX_TOOL_CALLS_PER_TURN before it
        could ever answer. "query" may now be a list of terms — one search
        action, one tool_calls_used increment, results for every term."""
        from openstore.agents.buyer_graph import MAX_TOOL_CALLS_PER_TURN, BuyerGraph

        class _ByTermMCPClient(_MultiItemMCPClient):
            async def search_products(self, query, tags=None, limit=20):
                catalog = {
                    "vanilla": [{"sku": "gelato_vanilla", "unit_minor": 15000, "tags": ["vegan"]}],
                    "pistachio": [
                        {"sku": "gelato_pistachio", "unit_minor": 18000, "tags": ["pistachio"]}
                    ],
                    "waffle cone": [{"sku": "cone_waffle", "unit_minor": 3000, "tags": ["cone"]}],
                }
                return {"success": True, "data": {"items": catalog.get(query, [])}, "error": None}

        calls = _register_queued_provider(
            monkeypatch,
            "test_graph_multi_query_search",
            [
                {"action": "search", "query": ["vanilla", "pistachio", "waffle cone"]},
                {
                    "action": "answer",
                    "selections": [
                        {"sku": "gelato_vanilla", "merchant_id": "test-merchant", "qty": 1},
                        {"sku": "gelato_pistachio", "merchant_id": "test-merchant", "qty": 1},
                        {"sku": "cone_waffle", "merchant_id": "test-merchant", "qty": 1},
                    ],
                },
            ],
        )
        graph = BuyerGraph(config, _ByTermMCPClient())
        result = await graph.converse(
            [{"role": "user", "content": "vanilla, pistachio and a waffle cone"}],
            "p_001",
            "trace_multi_query",
        )
        assert len(calls) == 2  # one search call, one answer — well under the cap
        assert result["awaiting_reply"] is True
        assert {item["sku"] for item in result["cart"]} == {
            "gelato_vanilla",
            "gelato_pistachio",
            "cone_waffle",
        }
        assert MAX_TOOL_CALLS_PER_TURN >= 1  # sanity: cap wasn't touched by this fix

    async def test_search_query_list_is_capped_and_truncated_not_rejected(
        self, config, monkeypatch
    ):
        from openstore.agents.buyer_graph import MAX_QUERIES_PER_SEARCH, BuyerGraph

        seen_queries: list[str] = []

        class _RecordingMCPClient(_MultiItemMCPClient):
            async def search_products(self, query, tags=None, limit=20):
                seen_queries.append(query)
                return {"success": True, "data": {"items": []}, "error": None}

        too_many = [f"item{i}" for i in range(MAX_QUERIES_PER_SEARCH + 3)]
        _register_queued_provider(
            monkeypatch,
            "test_graph_query_cap",
            [
                {"action": "search", "query": too_many},
                {"action": "ask", "message": "still looking"},
            ],
        )
        graph = BuyerGraph(config, _RecordingMCPClient())
        await graph.converse(
            [{"role": "user", "content": "lots of things"}], "p_001", "trace_query_cap"
        )
        assert len(seen_queries) == MAX_QUERIES_PER_SEARCH  # truncated, not rejected

    async def test_on_search_callback_fires_once_per_search_with_the_query(
        self, config, monkeypatch
    ):
        """S15: "Looking for X…" chat feedback — the callback must fire once
        per search, in order, with the exact query the LLM chose, and must
        NOT fire for the final "answer" step (no search happened there)."""
        from openstore.agents.buyer_graph import BuyerGraph

        _register_queued_provider(
            monkeypatch,
            "test_graph_on_search",
            [
                {"action": "search", "query": "vanilla"},
                {"action": "search", "query": "gelato"},
                {
                    "action": "answer",
                    "selections": [
                        {"sku": "gelato_vanilla", "merchant_id": "test-merchant", "qty": 1}
                    ],
                },
            ],
        )
        graph = BuyerGraph(config, _MultiItemMCPClient())
        seen_queries: list[str] = []

        async def _on_search(query: str) -> None:
            seen_queries.append(query)

        await graph.converse(
            [{"role": "user", "content": "vanilla gelato please"}],
            "p_001",
            "trace_on_search",
            on_search=_on_search,
        )
        assert seen_queries == ["vanilla", "gelato"]

    async def test_resumed_turn_recognizes_skus_from_an_earlier_turns_search(
        self, config, monkeypatch
    ):
        """Regression (found live): a fresh converse() call starts a new
        all_search_results, but a RESUMED conversation's second converse()
        call must still recognize skus a search found in an earlier turn —
        the LLM can see them in the transcript and correctly reference them,
        so validate_selection must not reject them as "hallucinated" just
        because this invocation didn't search again."""
        from openstore.agents.buyer_graph import BuyerGraph

        graph = BuyerGraph(config, _MultiItemMCPClient())

        # Turn 1: searches, then asks (as if nothing matched exactly).
        _register_queued_provider(
            monkeypatch,
            "test_graph_resume_turn1",
            [
                {"action": "search", "query": "vanilla"},
                {"action": "ask", "message": "No exact match — pistachio okay instead?"},
            ],
        )
        turn1 = await graph.converse(
            [{"role": "user", "content": "vanilla gelato please"}], "p_001", "trace_resume"
        )
        assert turn1["awaiting_reply"] is True

        # Turn 2 (resumed): the buyer replies, the LLM answers directly with
        # a sku from turn 1's search — no new search this turn.
        _register_queued_provider(
            monkeypatch,
            "test_graph_resume_turn2",
            [
                {
                    "action": "answer",
                    "selections": [
                        {"sku": "gelato_pistachio", "merchant_id": "test-merchant", "qty": 1}
                    ],
                }
            ],
        )
        turn2_messages = [*turn1["messages"], {"role": "user", "content": "pistachio please"}]
        turn2 = await graph.converse(turn2_messages, "p_001", "trace_resume")
        assert turn2["cart"] == [
            {
                "sku": "gelato_pistachio",
                "merchant_id": "test-merchant",
                "qty": 1,
                "unit_minor": 18000,
                "tags": ["pistachio"],
                "name": "gelato_pistachio",
            }
        ]

    async def test_plan_rejects_hallucinated_sku(self, config, monkeypatch):
        from openstore.agents.buyer_graph import BuyerGraph, BuyerPlanError

        _register_queued_provider(
            monkeypatch,
            "test_graph_hallucinated",
            [
                {"action": "search", "query": "pistachio"},
                {
                    "action": "answer",
                    "selections": [
                        {"sku": "gelato_zzz_invented", "merchant_id": "test-merchant", "qty": 1}
                    ],
                },
            ],
        )
        graph = BuyerGraph(config, _MultiItemMCPClient())
        with pytest.raises(BuyerPlanError, match="hallucinated_sku"):
            await graph.converse(
                [{"role": "user", "content": "pistachio gelato"}], "p_001", "trace_004"
            )

    async def test_plan_rejects_malformed_json(self, config, monkeypatch):
        from openstore.agents.buyer_graph import BuyerGraph, BuyerPlanError

        _register_queued_provider(monkeypatch, "test_graph_bad_json", ["not json"])
        graph = BuyerGraph(config, _MultiItemMCPClient())
        with pytest.raises(BuyerPlanError):
            await graph.converse(
                [{"role": "user", "content": "pistachio gelato"}], "p_001", "trace_005"
            )

    @pytest.mark.parametrize(
        "raw_response",
        [
            '{"action": "search", "query": "vanilla"} the buyer wants vanilla',
            '{"action": "search", "query": "vanilla"}\n\nI hope that helps!',
            '{"action": "search", "query": "vanilla"} {"action": "search", "query": "vanilla"}',
            '  {"action": "search", "query": "vanilla"}  ',
        ],
    )
    async def test_plan_tolerates_trailing_content_after_valid_json(
        self, config, monkeypatch, raw_response
    ):
        """Regression (found live): some completions append trailing content
        after a perfectly valid action object (a stray sentence, a second
        JSON blob, whitespace) — json.loads rejects the WHOLE response as
        "Extra data" even though the action itself parsed fine, crashing the
        turn. Only the first JSON value should matter."""
        from openstore.agents.buyer_graph import BuyerGraph

        _register_queued_provider(
            monkeypatch,
            "test_graph_trailing_json",
            [
                raw_response,
                {
                    "action": "answer",
                    "selections": [
                        {"sku": "gelato_vanilla", "merchant_id": "test-merchant", "qty": 1}
                    ],
                },
            ],
        )
        graph = BuyerGraph(config, _MultiItemMCPClient())
        result = await graph.converse(
            [{"role": "user", "content": "vanilla gelato"}], "p_001", "trace_trailing"
        )
        assert (
            result["awaiting_reply"] is True
        )  # reached the search step fine, then paused to confirm

    async def test_plan_rejects_invalid_action(self, config, monkeypatch):
        from openstore.agents.buyer_graph import BuyerGraph, BuyerPlanError

        _register_queued_provider(
            monkeypatch, "test_graph_bad_action", [{"action": "delete_everything"}]
        )
        graph = BuyerGraph(config, _MultiItemMCPClient())
        with pytest.raises(BuyerPlanError, match="invalid_action"):
            await graph.converse(
                [{"role": "user", "content": "pistachio gelato"}], "p_001", "trace_006"
            )

    async def test_plan_llm_failure_propagates(self, config, monkeypatch):
        from openstore.agents.buyer_graph import BuyerGraph
        from openstore.agents.llm import DummyProvider, LLMError, register_provider

        class _RaisingProvider(DummyProvider):
            def chat(self, messages, **kwargs):
                raise LLMError("simulated timeout")

        register_provider("test_graph_raises", _RaisingProvider)
        monkeypatch.setenv("LLM_PROVIDER", "test_graph_raises")

        graph = BuyerGraph(config, _MultiItemMCPClient())
        with pytest.raises(LLMError):
            await graph.converse(
                [{"role": "user", "content": "pistachio gelato"}], "p_001", "trace_007"
            )

    async def test_ask_action_returns_awaiting_reply_no_cart_submitted(self, config, monkeypatch):
        from openstore.agents.buyer_graph import BuyerGraph

        _register_queued_provider(
            monkeypatch,
            "test_graph_ask",
            [
                {"action": "search", "query": "vanilla"},
                {
                    "action": "ask",
                    "message": "No vanilla, but we have pistachio or cream — want one of those?",
                },
            ],
        )

        class _EmptyMCPClient(_MultiItemMCPClient):
            async def search_products(self, query, tags=None, limit=20):
                return {"success": True, "data": {"items": []}, "error": None}

        graph = BuyerGraph(config, _EmptyMCPClient())
        result = await graph.converse(
            [{"role": "user", "content": "vanilla gelato"}], "p_001", "trace_008"
        )
        assert result["awaiting_reply"] is True
        assert "pistachio" in result["question"]
        assert "messages" in result  # transcript, for a resumed continue_shop() call

    async def test_max_tool_calls_per_turn_enforced_without_extra_llm_call(
        self, config, monkeypatch
    ):
        from openstore.agents.buyer_graph import MAX_TOOL_CALLS_PER_TURN, BuyerGraph

        calls = _register_queued_provider(
            monkeypatch,
            "test_graph_cap",
            [{"action": "search", "query": "vanilla"}] * (MAX_TOOL_CALLS_PER_TURN + 2),
        )

        class _AlwaysEmptyMCPClient(_MultiItemMCPClient):
            async def search_products(self, query, tags=None, limit=20):
                return {"success": True, "data": {"items": []}, "error": None}

        graph = BuyerGraph(config, _AlwaysEmptyMCPClient())
        result = await graph.converse(
            [{"role": "user", "content": "vanilla gelato"}], "p_001", "trace_009"
        )
        assert result["awaiting_reply"] is True
        # the cap fires without a bonus LLM call once tool_calls_used == cap
        assert len(calls) == MAX_TOOL_CALLS_PER_TURN

    async def test_answer_pauses_for_cart_confirmation_instead_of_finishing(
        self, config, monkeypatch
    ):
        """S16: "answer" never submits directly anymore — it always pauses
        for an explicit buyer confirmation first (R0.9: nothing the LLM
        proposes moves money on its own)."""
        from openstore.agents.buyer_graph import _CART_CONFIRMATION_QUESTION, BuyerGraph

        _register_queued_provider(
            monkeypatch,
            "test_graph_confirm_pause",
            [
                {"action": "search", "query": "pistachio"},
                {
                    "action": "answer",
                    "selections": [
                        {"sku": "gelato_pistachio", "merchant_id": "test-merchant", "qty": 1}
                    ],
                },
            ],
        )
        graph = BuyerGraph(config, _MultiItemMCPClient())
        result = await graph.converse(
            [{"role": "user", "content": "pistachio gelato"}], "p_001", "trace_confirm"
        )
        assert result["awaiting_reply"] is True
        assert result["question"] == _CART_CONFIRMATION_QUESTION
        assert result["cart"] == [
            {
                "sku": "gelato_pistachio",
                "merchant_id": "test-merchant",
                "qty": 1,
                "unit_minor": 18000,
                "tags": ["pistachio"],
                "name": "gelato_pistachio",
            }
        ]

    async def test_empty_selection_answer_skips_confirmation(self, config, monkeypatch):
        """An "answer" with nothing in it has nothing to confirm — still
        terminates immediately, same as before S16."""
        from openstore.agents.buyer_graph import BuyerGraph

        _register_queued_provider(
            monkeypatch, "test_graph_empty_answer", [{"action": "answer", "selections": []}]
        )
        graph = BuyerGraph(config, _MultiItemMCPClient())
        result = await graph.converse(
            [{"role": "user", "content": "anything at all"}], "p_001", "trace_empty_answer"
        )
        assert "awaiting_reply" not in result
        assert result["cart"] == []

    async def test_confirming_a_pending_cart_skips_the_llm_entirely(self, config, monkeypatch):
        """S16 fast path: a plain "yes" reply to a cart-confirmation pause
        never calls the LLM again — BuyerAgent.continue_shop() must detect
        this itself, so this is tested at the BuyerAgent level, not the bare
        graph (the graph has no fast path of its own)."""
        calls = _register_queued_provider(
            monkeypatch,
            "test_agent_confirm_fastpath",
            [
                {"action": "search", "query": "pistachio"},
                {
                    "action": "answer",
                    "selections": [
                        {"sku": "gelato_pistachio", "merchant_id": "test-merchant", "qty": 1}
                    ],
                },
            ],
        )

        class _SpyMCPClient(_MultiItemMCPClient):
            def __init__(self):
                super().__init__()
                self.create_cart_calls: list[list[dict]] = []

            async def call(self, tool_name, arguments):
                if tool_name == "create_cart":
                    self.create_cart_calls.append(arguments["items"])
                    return {
                        "success": True,
                        "data": {
                            "allowed": True,
                            "reason_code": None,
                            "checkout_id": "chk_fp",
                            "aal_level": 0,
                            "effective_amount_minor": 18000,
                            "transcript": [],
                        },
                    }
                if tool_name == "checkout_initiate":
                    return {
                        "success": True,
                        "data": {
                            "checkout_id": "chk_fp",
                            "state": "HELD",
                            "amount_minor": 18000,
                            "currency": "INR",
                            "short_url": "https://pay.example/fp",
                            "cancel_token": "tok_fp",
                            "expires_at": "2030-01-01T00:00:00Z",
                        },
                    }
                raise AssertionError(f"unexpected tool: {tool_name}")

        mcp = _SpyMCPClient()
        agent = BuyerAgent(config, mcp)
        pending = await agent.start_shop("pistachio gelato", "p_001", "trace_fastpath")
        assert pending["awaiting_reply"] is True
        llm_calls_before_confirm = len(calls)

        result = await agent.continue_shop(pending["messages"], "yes", "p_001", "trace_fastpath")

        assert len(calls) == llm_calls_before_confirm  # no extra LLM call for "yes"
        assert result["allowed"] is True
        assert result["checkout_id"] == "chk_fp"
        assert mcp.create_cart_calls == [
            [
                {
                    "sku": "gelato_pistachio",
                    "merchant_id": "test-merchant",
                    "qty": 1,
                    "unit_minor": 18000,
                    "tags": ["pistachio"],
                    "name": "gelato_pistachio",
                }
            ]
        ]

    async def test_declining_a_pending_cart_revises_via_the_llm(self, config, monkeypatch):
        """A non-affirmative reply to a cart-confirmation pause re-enters the
        SAME general tool-calling loop (not a narrow "edit" mode) — here the
        buyer swaps their selection entirely, and gets a NEW confirmation
        pause for the revised cart."""
        from openstore.agents.buyer_graph import _CART_CONFIRMATION_QUESTION

        _register_queued_provider(
            monkeypatch,
            "test_agent_decline_revise",
            [
                {"action": "search", "query": "pistachio"},
                {
                    "action": "answer",
                    "selections": [
                        {"sku": "gelato_pistachio", "merchant_id": "test-merchant", "qty": 1}
                    ],
                },
                {
                    "action": "answer",
                    "selections": [
                        {"sku": "gelato_vanilla", "merchant_id": "test-merchant", "qty": 1}
                    ],
                },
            ],
        )
        agent = BuyerAgent(config, _MultiItemMCPClient())
        pending = await agent.start_shop("pistachio gelato", "p_001", "trace_decline")
        assert pending["cart"][0]["sku"] == "gelato_pistachio"

        result = await agent.continue_shop(
            pending["messages"], "actually, vanilla please", "p_001", "trace_decline"
        )

        assert result["awaiting_reply"] is True
        assert result["question"] == _CART_CONFIRMATION_QUESTION
        assert result["cart"][0]["sku"] == "gelato_vanilla"

    @pytest.mark.parametrize(
        "reply",
        [
            "yes",
            "yes.",
            "Yes!",
            "yes, go ahead.",
            "yeah sounds good",
            "sure thing",
            "confirm",
            "ok, do it",
        ],
    )
    async def test_is_affirmative_reply_matches_common_confirmation_phrasings(self, reply):
        """Regression: a live user replied "yes, go ahead." — the original
        exact-match-only whitelist rejected it (even bare "yes." with a
        trailing period failed), fell through to the LLM, which re-answered
        with the identical cart, which paused for confirmation again: an
        actual infinite loop, only bounded by MAX_CONVERSATION_TURNS."""
        from openstore.agents.buyer_graph import is_affirmative_reply

        assert is_affirmative_reply(reply) is True

    @pytest.mark.parametrize(
        "reply",
        [
            "yes but swap the cone for sprinkles",
            "yeah actually make it chocolate instead",
            "no",
            "actually, vanilla please",
            "can I also add a cone",
        ],
    )
    async def test_is_affirmative_reply_rejects_revisions(self, reply):
        from openstore.agents.buyer_graph import is_affirmative_reply

        assert is_affirmative_reply(reply) is False

    async def test_unrecognized_confirmation_wording_still_fast_paths_via_llm(
        self, config, monkeypatch
    ):
        """The exact live bug: a reply the whitelist doesn't recognize as
        affirmative falls through to the LLM, which reproduces the SAME
        cart — cart_signature() must catch that and submit directly rather
        than pausing for confirmation a second time (no infinite loop, and
        at most one extra LLM call, not an unbounded number)."""
        calls = _register_queued_provider(
            monkeypatch,
            "test_agent_unrecognized_confirm",
            [
                {"action": "search", "query": "pistachio"},
                {
                    "action": "answer",
                    "selections": [
                        {"sku": "gelato_pistachio", "merchant_id": "test-merchant", "qty": 1}
                    ],
                },
                # a wording is_affirmative_reply doesn't recognize forces one
                # LLM round-trip — it reproduces the identical selection.
                {
                    "action": "answer",
                    "selections": [
                        {"sku": "gelato_pistachio", "merchant_id": "test-merchant", "qty": 1}
                    ],
                },
            ],
        )

        class _SpyMCPClient(_MultiItemMCPClient):
            def __init__(self):
                super().__init__()
                self.create_cart_calls: list[list[dict]] = []

            async def call(self, tool_name, arguments):
                if tool_name == "create_cart":
                    self.create_cart_calls.append(arguments["items"])
                    return {
                        "success": True,
                        "data": {
                            "allowed": True,
                            "reason_code": None,
                            "checkout_id": "chk_sig",
                            "aal_level": 0,
                            "effective_amount_minor": 18000,
                            "transcript": [],
                        },
                    }
                if tool_name == "checkout_initiate":
                    return {
                        "success": True,
                        "data": {
                            "checkout_id": "chk_sig",
                            "state": "HELD",
                            "amount_minor": 18000,
                            "currency": "INR",
                            "short_url": "https://pay.example/sig",
                            "cancel_token": "tok_sig",
                            "expires_at": "2030-01-01T00:00:00Z",
                        },
                    }
                raise AssertionError(f"unexpected tool: {tool_name}")

        mcp = _SpyMCPClient()
        agent = BuyerAgent(config, mcp)
        pending = await agent.start_shop("pistachio gelato", "p_001", "trace_sig")
        assert pending["awaiting_reply"] is True

        # a phrasing our whitelist genuinely can't recognize as affirmative
        # (doesn't start with a recognized affirmative word at all)
        result = await agent.continue_shop(
            pending["messages"], "perfect, that's what I want", "p_001", "trace_sig"
        )

        assert len(calls) == 3  # one extra LLM call — not zero, not unbounded
        assert result.get("awaiting_reply") is not True  # terminal, not another pause
        assert result["allowed"] is True
        assert result["checkout_id"] == "chk_sig"
        assert len(mcp.create_cart_calls) == 1


class TestBuyerAgent:
    async def test_start_shop_returns_awaiting_reply_without_touching_create_cart(
        self, config, monkeypatch
    ):
        """Wiring check: BuyerAgent.start_shop() surfaces BuyerGraph's
        awaiting_reply outcome directly, never attempting create_cart."""
        _register_queued_provider(
            monkeypatch,
            "test_agent_ask",
            [{"action": "ask", "message": "What flavor are you after?"}],
        )

        calls: list = []

        class _SpyMCPClient(_MultiItemMCPClient):
            async def call(self, tool_name, arguments):
                calls.append(tool_name)
                return await super().call(tool_name, arguments)

        agent = BuyerAgent(config, _SpyMCPClient())
        result = await agent.start_shop("something sweet", "p_001", "trace_010")
        assert result["awaiting_reply"] is True
        assert result["question"] == "What flavor are you after?"
        assert "create_cart" not in calls

    async def test_agent_holds_no_payment_keys(self, config, mcp_client):
        """R0.10: agent has no Razorpay credentials."""
        agent = BuyerAgent(config, mcp_client)
        # No way to access razorpay.key_secret
        assert not hasattr(agent, "razorpay_key_secret")
        assert not hasattr(agent, "key_secret")
        assert not hasattr(agent, "psp_credentials")

    def test_cart_hash_is_deterministic(self, config):
        cart = [
            {"sku": "GEL-VAN-500", "qty": 1, "unit_minor": 21000},
            {"sku": "GEL-CHO-500", "qty": 2, "unit_minor": 22000},
        ]
        h1 = compute_cart_hash(cart)
        h2 = compute_cart_hash(cart)
        assert h1 == h2
        assert h1.startswith("sha256:")


class TestBuyerFacingCopy:
    """The buyer's own DM/channel must stay conversational and never leak
    reason codes/trace ids/transcripts — that detail goes to the buyer/
    merchant-trace Discord channels instead (see the DiscordNotifier calls in
    BuyerAgent.shop()/BuyerBot)."""

    def test_render_shop_result_denial_has_no_raw_reason_code(self):
        text = render_shop_result({"allowed": False, "reason_code": "policy.spend_per_tx_exceeded"})
        assert "policy.spend_per_tx_exceeded" not in text
        assert "spending limit" in text

    def test_render_shop_result_unmapped_reason_code_has_generic_fallback(self):
        text = render_shop_result({"allowed": False, "reason_code": "policy.some_future_code"})
        assert "policy.some_future_code" not in text
        assert "spending policy" in text

    def test_render_shop_result_success_has_no_technical_fields(self):
        text = render_shop_result(
            {
                "allowed": True,
                "amount_minor": 21000,
                "short_url": "https://pay.example/x",
                "checkout_id": "chk_secret_internal_id",
                "trace_id": "trace_should_not_leak",
                "aal_level": 2,
            }
        )
        assert "chk_secret_internal_id" not in text
        assert "trace_should_not_leak" not in text
        assert "aal" not in text.lower()
        assert "₹210.00" in text
        assert "https://pay.example/x" in text

    def test_build_shop_result_embed_denial_has_no_raw_reason_code(self):
        embed = build_shop_result_embed({"allowed": False, "reason_code": "policy.tag_violation"})
        assert "policy.tag_violation" not in embed["description"]
        assert embed["title"] == "Couldn't place that order"

    def test_build_shop_result_embed_success_has_no_technical_fields(self):
        embed = build_shop_result_embed(
            {
                "allowed": True,
                "amount_minor": 21000,
                "short_url": "https://pay.example/x",
                "checkout_id": "chk_secret_internal_id",
                "trace_id": "trace_should_not_leak",
                "aal_level": 2,
            }
        )
        field_names = {f["name"] for f in embed["fields"]}
        assert field_names == {"Total", "Pay here"}
        assert "chk_secret_internal_id" not in str(embed)
        assert "trace_should_not_leak" not in str(embed)
        assert "aal" not in str(embed).lower()


def _register_negotiate_provider(monkeypatch, name: str, response: str) -> None:
    import json as _json

    from openstore.agents.llm import DummyProvider, register_provider

    class _Provider(DummyProvider):
        def chat(self, messages, **kwargs):
            return response if isinstance(response, str) else _json.dumps(response)

    register_provider(name, _Provider)
    monkeypatch.setenv("LLM_PROVIDER", name)


class TestMerchantAgent:
    def test_negotiate_returns_counter(self, config, monkeypatch):
        _register_negotiate_provider(
            monkeypatch,
            "test_negotiate_tag",
            {"action": "remove_violating_tags", "rationale": "cart carries a disallowed tag"},
        )
        agent = MerchantAgent(config)
        cart = [{"sku": "GEL-VAN-500", "qty": 1, "unit_minor": 21000, "tags": ["vegan"]}]
        result = agent.negotiate(cart, "policy.tag_violation", "trace_001", policy={})
        assert result["from"] == "merchant_agent"
        assert result["state"] == "COUNTERED"
        assert result["reason_code"] == "policy.tag_violation"
        assert result["cart_delta"] == {"remove_violating_tags": True}
        assert result["rationale"]

    def test_negotiate_unrecoverable_returns_no_compliant_path(self, config, monkeypatch):
        _register_negotiate_provider(
            monkeypatch,
            "test_negotiate_none",
            {"action": "no_compliant_path", "rationale": "no fix within the closed action space"},
        )
        agent = MerchantAgent(config)
        result = agent.negotiate([], "policy.no_human_authority", "trace_002", policy={})
        assert result["state"] == "NO_COMPLIANT_PATH"
        assert result["cart_delta"] == {}

    def test_negotiate_invalid_action_raises(self, config, monkeypatch):
        from openstore.agents.merchant_agent import MerchantAgentError

        _register_negotiate_provider(
            monkeypatch, "test_negotiate_bad_action", {"action": "delete_whole_cart"}
        )
        agent = MerchantAgent(config)
        with pytest.raises(MerchantAgentError):
            agent.negotiate([], "policy.tag_violation", "trace_003", policy={})
        # an action outside NEGOTIATE_ACTIONS never reaches apply_cart_delta's
        # 3-flag executor — negotiate() raises before returning a cart_delta at all

    def test_negotiate_malformed_json_raises(self, config, monkeypatch):
        from openstore.agents.merchant_agent import MerchantAgentError

        _register_negotiate_provider(monkeypatch, "test_negotiate_bad_json", "not json")
        agent = MerchantAgent(config)
        with pytest.raises(MerchantAgentError):
            agent.negotiate([], "policy.tag_violation", "trace_004", policy={})

    def test_negotiate_llm_failure_propagates(self, config, monkeypatch):
        from openstore.agents.llm import DummyProvider, LLMError, register_provider

        class _RaisingProvider(DummyProvider):
            def chat(self, messages, **kwargs):
                raise LLMError("simulated timeout")

        register_provider("test_negotiate_raises", _RaisingProvider)
        monkeypatch.setenv("LLM_PROVIDER", "test_negotiate_raises")

        from openstore.agents.llm import LLMError as LLMErrorType

        agent = MerchantAgent(config)
        with pytest.raises(LLMErrorType):
            agent.negotiate([], "policy.tag_violation", "trace_005", policy={})

    def test_draft_amendment_has_required_fields(self, config):
        agent = MerchantAgent(config)
        cart = [{"sku": "GEL-PIST-500", "qty": 1, "unit_minor": 30000, "tags": ["pistachio"]}]
        amendment = agent.draft_amendment(
            base_policy_hash="hash_001",
            reason_code="policy.sku_blocked",
            cart=cart,
            trace_id="trace_001",
        )
        assert amendment["drafted_by"] == "merchant_agent"
        assert amendment["state"] == "PENDING_APPROVAL"
        assert amendment["reason_code_triggered"] == "policy.sku_blocked"
        assert amendment["approval"]["webauthn_assertion"] is None  # R0.5: no self-approval
        assert amendment["draft_digest"].startswith("sha256:")

    def test_draft_amendment_requires_human_signature(self, config):
        """R0.5: agent cannot self-approve. The amendment is a PROPOSAL."""
        agent = MerchantAgent(config)
        amendment = agent.draft_amendment("h", "policy.sku_blocked", [], "trace_001")
        # webauthn_assertion must be None — human must sign
        assert amendment["approval"]["webauthn_assertion"] is None
        assert amendment["approval"]["approver_credential_id"] is None

    def test_narrator_writes_prose(self, config):
        agent = MerchantAgent(config)
        bundle = {
            "transaction": {
                "merchant_id": "gelateria",
                "checkout_id": "chk_001",
                "amount_minor": 21000,
            },
            "adjudication": {"verdict": "ALLOW"},
            "aal": {"level": 2},
        }
        note = agent.narrate(bundle)
        assert "chk_001" in note
        assert "AAL" in note or "level" in note.lower()
        assert "gelateria" in note
        # R0.9: narrator prose is text, never modifying the bundle
        assert isinstance(note, str)


class TestNegotiationStates:
    def test_valid_states(self):
        from openstore.agents.merchant_agent import NEGOTIATION_STATES

        for state in (
            "PROPOSED",
            "COUNTERED",
            "ACCEPTED",
            "NO_COMPLIANT_PATH",
            "AMENDMENT_REQUESTED",
        ):
            assert state in NEGOTIATION_STATES

    def test_invalid_state_raises(self):
        with pytest.raises(ValueError):
            NegotiationMessage(
                negotiation_id="neg_1",
                round=1,
                from_="buyer_agent",
                state="INVALID_STATE",
                cart_delta={},
                reason_code="x",
                trace_id="t",
            )

    def test_invalid_from_raises(self):
        with pytest.raises(ValueError):
            NegotiationMessage(
                negotiation_id="neg_1",
                round=1,
                from_="random",
                state="PROPOSED",
                cart_delta={},
                reason_code="x",
                trace_id="t",
            )


class TestR0NoAgentBypass:
    """R0.9: agents can never bypass compile_decision()."""

    def test_buyer_agent_no_spend_cap(self, config, mcp_client):
        """Buyer agent cannot have a 'skip_spend_cap' knob."""
        agent = BuyerAgent(config, mcp_client)
        assert not hasattr(agent, "skip_spend_cap")
        assert not hasattr(agent, "override_total")
        assert not hasattr(agent, "force_allow")

    def test_buyer_graph_no_bypass_knobs(self, config, mcp_client):
        """R0.9: the LangGraph-backed planner has no bypass knobs either, on
        the graph instance or its state schema."""
        from openstore.agents.buyer_graph import BuyerGraph, BuyerPlanState

        graph = BuyerGraph(config, mcp_client)
        for attr in ("skip_spend_cap", "override_total", "force_allow", "bypass_compiler"):
            assert not hasattr(graph, attr)
        forbidden = {"skip_spend_cap", "override_total", "force_allow", "bypass_compiler"}
        assert set(BuyerPlanState.__annotations__) & forbidden == set()

    def test_buyer_graph_holds_no_payment_keys(self, config, mcp_client):
        """R0.10: the LangGraph-backed planner never sees PSP credentials."""
        from openstore.agents.buyer_graph import BuyerGraph

        graph = BuyerGraph(config, mcp_client)
        for attr in ("razorpay_key_secret", "key_secret", "psp_credentials", "signing_key"):
            assert not hasattr(graph, attr)

    def test_shopping_session_no_bypass_knobs_or_payment_keys(self):
        """R0.9/R0.10: ShoppingSession (S13's parked-conversation row) has no
        bypass knobs and never carries payment/signing material — same shape
        as Checkout's existing nullable chat-identity fields."""
        from openstore.models import ShoppingSession

        forbidden = {
            "skip_spend_cap",
            "override_total",
            "force_allow",
            "bypass_compiler",
            "razorpay_key_secret",
            "key_secret",
            "psp_credentials",
            "signing_key",
        }
        assert set(ShoppingSession.model_fields) & forbidden == set()

    def test_merchant_agent_no_signing_keys(self, config):
        """R0.10: merchant agent holds no signing material."""
        agent = MerchantAgent(config)
        for attr in dir(agent):
            if "key" in attr.lower() and attr not in ("_mock_keys",):
                if not attr.startswith("__"):
                    val = getattr(agent, attr, None)
                    if callable(val) and not isinstance(val, type):
                        continue
                    assert "secret" not in attr.lower() or "secret" in ("draft_secret",)


class TestLLMProvider:
    def test_dummy_provider(self):
        provider = DummyProvider(model="dummy-model")
        result = provider.chat([{"role": "user", "content": "hi"}])
        assert isinstance(result, str)

    def test_register_provider(self):
        from openstore.agents.llm import _PROVIDERS, register_provider

        class TestProvider(LLMProvider):
            def chat(self, messages, **kwargs):
                return "test"

        register_provider("test", TestProvider)
        assert "test" in _PROVIDERS

    def test_anthropic_provider_sends_native_message_shape(self, monkeypatch):
        import json as _json

        import httpx

        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            captured["json"] = _json.loads(request.content)
            return httpx.Response(
                200,
                json={"content": [{"type": "text", "text": '{"answer": "ok"}'}]},
            )

        real_client_cls = httpx.Client

        def _patched_client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_client_cls(*args, **kwargs)

        monkeypatch.setattr(httpx, "Client", _patched_client)

        provider = AnthropicProvider(model="claude-3-5-sonnet", api_key="sk-ant-test")
        result = provider.chat(
            [
                {"role": "system", "content": "You are terse."},
                {"role": "user", "content": "hi"},
            ]
        )

        assert result == '{"answer": "ok"}'
        assert captured["url"] == "https://api.anthropic.com/v1/messages"
        assert captured["headers"]["x-api-key"] == "sk-ant-test"
        assert captured["headers"]["anthropic-version"] == "2023-06-01"
        assert "authorization" not in captured["headers"]
        assert captured["json"]["system"] == "You are terse."
        assert captured["json"]["messages"] == [{"role": "user", "content": "hi"}]

    def test_openai_compatible_providers_default_base_urls(self, monkeypatch):
        from openstore.agents.llm import (
            GeminiProvider,
            GroqProvider,
            NvidiaNIMProvider,
            OpenRouterProvider,
        )

        for env_var in (
            "GROQ_BASE_URL",
            "OPENROUTER_BASE_URL",
            "GEMINI_BASE_URL",
            "NVIDIA_BASE_URL",
        ):
            monkeypatch.delenv(env_var, raising=False)

        assert (
            GroqProvider(model="llama-3.3-70b-versatile").base_url
            == "https://api.groq.com/openai/v1"
        )
        assert (
            OpenRouterProvider(model="qwen/qwen-2.5-72b-instruct").base_url
            == "https://openrouter.ai/api/v1"
        )
        assert (
            GeminiProvider(model="gemini-2.0-flash").base_url
            == "https://generativelanguage.googleapis.com/v1beta/openai"
        )
        assert (
            NvidiaNIMProvider(model="meta/llama-3.3-70b-instruct").base_url
            == "https://integrate.api.nvidia.com/v1"
        )

    def test_groq_provider_sends_openai_compatible_shape(self, monkeypatch):
        import json as _json

        import httpx
        from openstore.agents.llm import GroqProvider

        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            captured["json"] = _json.loads(request.content)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"answer": "ok"}'}}]},
            )

        real_client_cls = httpx.Client

        def _patched_client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_client_cls(*args, **kwargs)

        monkeypatch.setattr(httpx, "Client", _patched_client)

        provider = GroqProvider(model="llama-3.3-70b-versatile", api_key="gsk-test")
        result = provider.chat([{"role": "user", "content": "hi"}])

        assert result == '{"answer": "ok"}'
        assert captured["url"] == "https://api.groq.com/openai/v1/chat/completions"
        assert captured["headers"]["authorization"] == "Bearer gsk-test"

    def test_failover_provider_routes_around_failing_provider(self):
        from openstore.agents.llm import FailoverProvider, LLMError

        class _FailingProvider(DummyProvider):
            def chat(self, messages, **kwargs):
                raise LLMError("simulated outage")

        class _WorkingProvider(DummyProvider):
            def chat(self, messages, **kwargs):
                return '{"answer": "from second provider"}'

        provider = FailoverProvider([_FailingProvider(model="down"), _WorkingProvider(model="up")])
        result = provider.chat([{"role": "user", "content": "hi"}])
        assert result == '{"answer": "from second provider"}'

    def test_failover_provider_raises_when_all_fail(self):
        from openstore.agents.llm import AllProvidersFailedError, FailoverProvider, LLMError

        class _FailingProvider(DummyProvider):
            def chat(self, messages, **kwargs):
                raise LLMError("simulated outage")

        provider = FailoverProvider([_FailingProvider(model="a"), _FailingProvider(model="b")])
        with pytest.raises(AllProvidersFailedError):
            provider.chat([{"role": "user", "content": "hi"}])

    def test_failover_provider_does_not_catch_non_llm_errors(self):
        from openstore.agents.llm import FailoverProvider

        class _BuggyProvider(DummyProvider):
            def chat(self, messages, **kwargs):
                raise ValueError("this is a bug, not a provider outage")

        provider = FailoverProvider([_BuggyProvider(model="a"), DummyProvider(model="b")])
        with pytest.raises(ValueError, match="this is a bug"):
            provider.chat([{"role": "user", "content": "hi"}])

    def test_create_llm_builds_failover_chain_from_env(self, config, monkeypatch):
        from openstore.agents.llm import FailoverProvider, create_llm

        monkeypatch.setenv("LLM_PROVIDER_CHAIN", "groq:llama-3.3-70b-versatile,openrouter,dummy")
        provider = create_llm(config)
        assert isinstance(provider, FailoverProvider)
        assert len(provider.providers) == 3
        assert provider.providers[0].model == "llama-3.3-70b-versatile"
        assert (
            provider.providers[1].model == config.llm.model
        )  # bare "openrouter" uses config model

    def test_create_llm_chain_rejects_unknown_provider(self, config, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER_CHAIN", "not_a_real_provider")
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            from openstore.agents.llm import create_llm

            create_llm(config)
