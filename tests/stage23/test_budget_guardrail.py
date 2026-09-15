# tests/stage23/test_budget_guardrail.py
# Stage 23 (Q-043 / DECISION-043) — cross-merchant consolidated budget
# guardrail. Planning-time only: post-Phase-1, pre-Phase-2, the buyer sums
# merchant-reported exposures + merchant-computed pendings against one
# operator-declared total. Buyer fakes stand in for per-merchant MCP
# (real HTTP fan-out already covered elsewhere); server exposure is pinned
# against a real seeded ledger.

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from openstore.agents.buyer_agent import BuyerAgent, _friendly_denial_reason
from openstore.buyer_config import BuyerSettings
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
        public_base_url="https://buyer.example",
    )


def _capped(config: Settings, cap: int | None) -> Settings:
    """Attach the S23 knob to merchant Settings (BuyerSettings carries it
    for real; Settings forbids extra attrs, so tests inject directly)."""
    config.__dict__["federation_total_cap_minor"] = cap
    return config


_POLICY_FIELDS = {
    "policy_id": "pol-1",
    "allowed_tags": [],
    "tag_mode": "all",
    "blocked_skus": [],
    "max_spend_per_tx_minor": 1_000_000,
    "policy_hash": "h" * 8,
}


class _FakeMerchantClient:
    """Stage-12 harness shape plus a scripted exposure per resolve_policy."""

    def __init__(
        self,
        merchant_id: str,
        *,
        exposures: list[Any] | None = None,
        create_cart_response: dict[str, Any] | None = None,
        checkout_response: dict[str, Any] | None = None,
    ) -> None:
        self.merchant_id = merchant_id
        self._exposures = list(exposures) if exposures is not None else [0]
        self.create_cart_response = create_cart_response
        self.checkout_response = checkout_response
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(
        self, tool_name: str, arguments: dict[str, Any], *, require_auth: bool = True
    ) -> dict[str, Any]:
        self.calls.append((tool_name, dict(arguments)))
        if tool_name == "resolve_policy":
            exposure = self._exposures.pop(0) if len(self._exposures) > 1 else self._exposures[0]
            if exposure == "unsigned":
                return {
                    "success": False,
                    "error": {"reason_code": "authority.policy_unsigned", "message": "no policy"},
                }
            return {"success": True, "data": {**_POLICY_FIELDS, "exposure_minor": exposure}}
        if tool_name == "create_cart":
            assert self.create_cart_response is not None
            return self.create_cart_response
        if tool_name == "checkout_initiate":
            assert self.checkout_response is not None
            return self.checkout_response
        raise AssertionError(f"unexpected tool call: {tool_name}")


class _FakeFederatingClient:
    def __init__(self, clients: dict[str, _FakeMerchantClient]) -> None:
        self._clients = clients

    def client_for(self, merchant_id: str) -> _FakeMerchantClient:
        return self._clients[merchant_id]


def _cart_ok(checkout_id: str, amount_minor: int) -> dict[str, Any]:
    return {
        "success": True,
        "data": {
            "allowed": True,
            "checkout_id": checkout_id,
            "aal_level": "AAL0",
            "transcript": None,
            "effective_amount_minor": amount_minor,
        },
    }


def _checkout_ok(checkout_id: str, amount_minor: int) -> dict[str, Any]:
    return {
        "success": True,
        "data": {
            "checkout_id": checkout_id,
            "state": "PENDING",
            "amount_minor": amount_minor,
            "currency": "INR",
            "short_url": f"https://pay/{checkout_id}",
            "cancel_token": f"cancel-{checkout_id}",
            "expires_at": "2026-01-01T00:00:00Z",
        },
    }


def _item(merchant_id: str, sku: str, qty: int, unit_minor: int) -> dict[str, Any]:
    return {
        "merchant_id": merchant_id,
        "sku": sku,
        "qty": qty,
        "unit_minor": unit_minor,
        "tags": [],
        "name": sku,
    }


def _tools_called(client: _FakeMerchantClient) -> list[str]:
    return [c[0] for c in client.calls]


class TestGuardrailOff:
    @pytest.mark.asyncio
    async def test_unset_cap_preserves_two_phase(self, config: Settings) -> None:
        """Knob unset (default): Phase 2 runs exactly as before S23."""
        cart = [_item("store-a", "sku-a", 1, 500), _item("store-b", "sku-b", 2, 300)]
        client_a = _FakeMerchantClient(
            "store-a",
            exposures=[10_000_000],
            create_cart_response=_cart_ok("co-a", 500),
            checkout_response=_checkout_ok("co-a", 500),
        )
        client_b = _FakeMerchantClient(
            "store-b",
            exposures=[10_000_000],
            create_cart_response=_cart_ok("co-b", 600),
            checkout_response=_checkout_ok("co-b", 600),
        )
        agent = BuyerAgent(config, _FakeFederatingClient({"store-a": client_a, "store-b": client_b}))
        result = await agent._submit_federated_cart(
            cart, "trace-1", "discord", "buyer1", "chan1", None, "goal"
        )
        assert result["allowed"] is True
        assert _tools_called(client_a) == ["resolve_policy", "create_cart", "checkout_initiate"]
        assert _tools_called(client_b) == ["resolve_policy", "create_cart", "checkout_initiate"]


class TestGuardrailOn:
    @pytest.mark.asyncio
    async def test_under_cap_proceeds(self, config: Settings) -> None:
        """Exposures 400+300 with pendings 500+600 = 1800 <= 2000."""
        cart = [_item("store-a", "sku-a", 1, 500), _item("store-b", "sku-b", 2, 300)]
        client_a = _FakeMerchantClient(
            "store-a",
            exposures=[400],
            create_cart_response=_cart_ok("co-a", 500),
            checkout_response=_checkout_ok("co-a", 500),
        )
        client_b = _FakeMerchantClient(
            "store-b",
            exposures=[300],
            create_cart_response=_cart_ok("co-b", 600),
            checkout_response=_checkout_ok("co-b", 600),
        )
        agent = BuyerAgent(
            _capped(config, 2000), _FakeFederatingClient({"store-a": client_a, "store-b": client_b})
        )
        result = await agent._submit_federated_cart(
            cart, "trace-1", "discord", "buyer1", "chan1", None, "goal"
        )
        assert result["allowed"] is True
        assert "checkout_initiate" in _tools_called(client_a)
        assert "checkout_initiate" in _tools_called(client_b)

    @pytest.mark.asyncio
    async def test_over_cap_blocks_everything(self, config: Settings) -> None:
        """Same cart against a 1000 cap: no Phase 2 anywhere, full breakdown."""
        cart = [_item("store-a", "sku-a", 1, 500), _item("store-b", "sku-b", 2, 300)]
        client_a = _FakeMerchantClient(
            "store-a",
            exposures=[400],
            create_cart_response=_cart_ok("co-a", 500),
            checkout_response=_checkout_ok("co-a", 500),
        )
        client_b = _FakeMerchantClient(
            "store-b",
            exposures=[300],
            create_cart_response=_cart_ok("co-b", 600),
            checkout_response=_checkout_ok("co-b", 600),
        )
        agent = BuyerAgent(
            _capped(config, 1000), _FakeFederatingClient({"store-a": client_a, "store-b": client_b})
        )
        result = await agent._submit_federated_cart(
            cart, "trace-1", "discord", "buyer1", "chan1", None, "goal"
        )
        assert result["allowed"] is False
        assert result["reason_code"] == "buyer.budget_exceeded"
        assert result["declared_total_minor"] == 1000
        assert result["projected_total_minor"] == 1800
        assert result["per_merchant"] == {
            "store-a": {"exposure_minor": 400, "pending_minor": 500, "total_minor": 900},
            "store-b": {"exposure_minor": 300, "pending_minor": 600, "total_minor": 900},
        }
        # Phase 1 ran everywhere (validation is not the guardrail's to skip);
        # Phase 2 ran nowhere.
        assert "checkout_initiate" not in _tools_called(client_a)
        assert "checkout_initiate" not in _tools_called(client_b)
        # Exposures were re-fetched fresh: enrollment pass + pre-commit round.
        assert _tools_called(client_a).count("resolve_policy") == 2

    @pytest.mark.asyncio
    async def test_single_merchant_covered(self, config: Settings) -> None:
        cart = [_item("store-a", "sku-a", 1, 500)]
        client_a = _FakeMerchantClient(
            "store-a",
            exposures=[600],
            create_cart_response=_cart_ok("co-a", 500),
            checkout_response=_checkout_ok("co-a", 500),
        )
        agent = BuyerAgent(_capped(config, 1000), _FakeFederatingClient({"store-a": client_a}))
        result = await agent._submit_federated_cart(
            cart, "trace-1", "discord", "buyer1", "chan1", None, "goal"
        )
        assert result["allowed"] is False
        assert result["reason_code"] == "buyer.budget_exceeded"
        assert result["projected_total_minor"] == 1100

    @pytest.mark.asyncio
    async def test_malformed_exposure_blocks(self, config: Settings) -> None:
        """A merchant that can't report a number must not silently pass —
        and 0 must never be assumed (that understates spend)."""
        cart = [_item("store-a", "sku-a", 1, 500)]
        client_a = _FakeMerchantClient(
            "store-a",
            exposures=["lots"],
            create_cart_response=_cart_ok("co-a", 500),
            checkout_response=_checkout_ok("co-a", 500),
        )
        agent = BuyerAgent(
            _capped(config, 1_000_000), _FakeFederatingClient({"store-a": client_a})
        )
        result = await agent._submit_federated_cart(
            cart, "trace-1", "discord", "buyer1", "chan1", None, "goal"
        )
        assert result["allowed"] is False
        assert result["reason_code"] == "buyer.exposure_unavailable"
        assert "checkout_initiate" not in _tools_called(client_a)

    @pytest.mark.asyncio
    async def test_negative_exposure_blocks(self, config: Settings) -> None:
        cart = [_item("store-a", "sku-a", 1, 500)]
        client_a = _FakeMerchantClient(
            "store-a",
            exposures=[-50],
            create_cart_response=_cart_ok("co-a", 500),
            checkout_response=_checkout_ok("co-a", 500),
        )
        agent = BuyerAgent(
            _capped(config, 1_000_000), _FakeFederatingClient({"store-a": client_a})
        )
        result = await agent._submit_federated_cart(
            cart, "trace-1", "discord", "buyer1", "chan1", None, "goal"
        )
        assert result["reason_code"] == "buyer.exposure_unavailable"

    @pytest.mark.asyncio
    async def test_missing_pending_blocks(self, config: Settings) -> None:
        """A Phase-1 ALLOW without effective_amount_minor is malformed input
        to the guardrail — block, don't assume 0."""
        cart = [_item("store-a", "sku-a", 1, 500)]
        client_a = _FakeMerchantClient(
            "store-a",
            exposures=[0],
            create_cart_response={
                "success": True,
                "data": {"allowed": True, "checkout_id": "co-a"},
            },
            checkout_response=_checkout_ok("co-a", 500),
        )
        agent = BuyerAgent(
            _capped(config, 1_000_000), _FakeFederatingClient({"store-a": client_a})
        )
        result = await agent._submit_federated_cart(
            cart, "trace-1", "discord", "buyer1", "chan1", None, "goal"
        )
        assert result["reason_code"] == "buyer.exposure_unavailable"
        assert "checkout_initiate" not in _tools_called(client_a)

    @pytest.mark.asyncio
    async def test_unsigned_on_refetch_blocks(self, config: Settings) -> None:
        """Enrolled at pass one, unsigned at the fresh round: bizarre, loud."""
        cart = [_item("store-a", "sku-a", 1, 500)]
        client_a = _FakeMerchantClient(
            "store-a",
            exposures=[0, "unsigned"],
            create_cart_response=_cart_ok("co-a", 500),
            checkout_response=_checkout_ok("co-a", 500),
        )
        agent = BuyerAgent(
            _capped(config, 1_000_000), _FakeFederatingClient({"store-a": client_a})
        )
        result = await agent._submit_federated_cart(
            cart, "trace-1", "discord", "buyer1", "chan1", None, "goal"
        )
        assert result["reason_code"] == "buyer.exposure_unavailable"


class TestBuyerConfig:
    def _base(self) -> dict:
        return {
            "discord": {
                "bot_token": "t",
                "buyer_trace_channel_id": 1,
                "merchant_trace_channel_id": 2,
                "money_trace_channel_id": 3,
                "alerts_channel_id": 4,
            },
            "merchants": [],
        }

    def test_cap_defaults_unset(self) -> None:
        assert BuyerSettings.model_validate(self._base()).federation_total_cap_minor is None

    def test_cap_accepts_positive(self) -> None:
        settings = BuyerSettings.model_validate({**self._base(), "federation_total_cap_minor": 500000})
        assert settings.federation_total_cap_minor == 500000

    @pytest.mark.parametrize("bad", [-1, 0])
    def test_cap_rejects_non_positive(self, bad: int) -> None:
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            BuyerSettings.model_validate({**self._base(), "federation_total_cap_minor": bad})

    def test_friendly_lines_cover_new_codes(self) -> None:
        assert _friendly_denial_reason("buyer.budget_exceeded") != _friendly_denial_reason("nope-unknown")
        assert _friendly_denial_reason("buyer.exposure_unavailable") != _friendly_denial_reason(
            "nope-unknown"
        )


class TestServerExposure:
    """resolve_policy answers merchant-computed exposure_minor (Q-043 D2)."""

    @pytest.fixture()
    def db(self, config: Settings):
        import openstore.core.database as _db
        from openstore.core.database import get_session

        prev = _db._engine
        _db._engine = None
        from openstore.core.database import init_database

        init_database(config)
        try:
            yield lambda: get_session(config)
        finally:
            from openstore.core.database import get_engine

            get_engine(config).dispose()
            _db._engine = prev

    def _seed(self, db, *, settled_minor: int = 0, held_minor: int = 700) -> None:
        from openstore.models import (
            Checkout,
            IntentPolicy,
            LedgerEntry,
            LedgerEntryType,
            OrderState,
            WebAuthnCredential,
        )

        now = int(datetime.now(UTC).timestamp())
        session = db()
        try:
            session.add(
                WebAuthnCredential(
                    credential_id="cred-1",
                    user_handle="u:1",
                    public_key=b"x",
                    sign_count=0,
                    is_active=True,
                )
            )
            session.add(
                IntentPolicy(
                    id="pol-x",
                    merchant_id="test",
                    policy_hash="e" * 64,
                    max_spend_per_tx_minor=1_000_000,
                    max_spend_total_minor=10_000_000,
                    max_transactions=100,
                    allowed_tags=[],
                    blocked_skus=[],
                    required_skus=[],
                    not_before=now - 10,
                    expires_at=now + 3600,
                    webauthn_credential_id="cred-1",
                    webauthn_sign_count=0,
                    signed_at=datetime.now(UTC).replace(tzinfo=None),
                )
            )
            session.add(
                Checkout(
                    id="chk-held",
                    trace_id="t",
                    client_id="c",
                    merchant_id="test",
                    cart_hash="h",
                    cart_version=1,
                    amount_minor=held_minor,
                    state=OrderState.HELD,
                    policy_id="pol-x",
                    expires_at=datetime.now(UTC).replace(tzinfo=None),
                    idempotency_key="idem-held",
                    cart_snapshot={},
                )
            )
            session.add(
                LedgerEntry(
                    trace_id="t",
                    client_id="c",
                    entry_type=LedgerEntryType.RESERVE,
                    amount_minor=held_minor,
                    currency="INR",
                    reference_id="chk-held",
                    account="merchant_pending",
                    counterparty_account="customer_hold",
                    idempotency_key="idem-ledger-held",
                    description="seed",
                )
            )
            if settled_minor:
                session.add(
                    Checkout(
                        id="chk-done",
                        trace_id="t",
                        client_id="c",
                        merchant_id="test",
                        cart_hash="h",
                        cart_version=1,
                        amount_minor=settled_minor,
                        state=OrderState.RELEASED,
                        policy_id="pol-x",
                        expires_at=datetime.now(UTC).replace(tzinfo=None),
                        idempotency_key="idem-done",
                        cart_snapshot={},
                    )
                )
                session.add(
                    LedgerEntry(
                        trace_id="t",
                        client_id="c",
                        entry_type=LedgerEntryType.CAPTURE,
                        amount_minor=settled_minor,
                        currency="INR",
                        reference_id="chk-done",
                        account="merchant_revenue",
                        counterparty_account="customer_hold",
                        idempotency_key="idem-ledger-done",
                        description="seed",
                    )
                )
            session.commit()
        finally:
            session.close()

    def test_exposure_sums_settled_plus_inflight(
        self, config: Settings, db, tmp_path
    ) -> None:
        import openstore.surfaces.catalog as catalog_mod
        from openstore.surfaces.mcp_server import resolve_policy

        catalog_mod.CATALOG_CACHE = None
        self._seed(db, settled_minor=300, held_minor=700)
        session = db()
        try:
            result = resolve_policy(config, session, "u:1", ["catalog:read"])
        finally:
            session.close()
        assert result.success is True
        assert result.data["exposure_minor"] == 1000
        assert result.data["policy_id"] == "pol-x"
