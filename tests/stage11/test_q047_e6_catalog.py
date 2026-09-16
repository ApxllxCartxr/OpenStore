# tests/stage11/test_q047_e6_catalog.py
# Q-047: PoAI predicate e6 (`goods.catalog_attestations_valid`) read a key that
# nothing in src/ ever wrote, so it was False on every bundle the sidecar built
# regardless of the goods. These tests pin both halves of the fix: the
# bundle-time resolution check itself, and the fact that a real bundle now
# carries its verdict. Self-contained fixtures (closest definition wins).

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from openstore.config import (
    CampaignSettings,
    DatabaseConfig,
    DiscordConfig,
    LLMSettings,
    MerchantConfig,
    RazorpayConfig,
    Settings,
    WebAuthnConfig,
    merchant_id,
)
from openstore.core.database import get_or_create_checkout, get_session, init_database
from openstore.core.ledger import create_reserve_entry
from openstore.core.poai import evaluate_aal_predicates
from openstore.models import OrderState
from openstore.psp import router as psp_router_module
from openstore.surfaces.catalog import cart_resolves_against_catalog

CATALOG_YAML = (
    "items:\n"
    "  - sku: gelato_vanilla\n"
    "    name: Vanilla Gelato\n"
    "    unit_minor: 15000\n"
    "    tags: [vegan, gelato]\n"
)

VANILLA = {"sku": "gelato_vanilla", "qty": 1, "unit_minor": 15000, "tags": ["vegan", "gelato"]}


def _settings(catalog_path: str | None) -> Settings:
    return Settings(
        merchant=MerchantConfig(name="Gelateria Milano", currency="INR"),
        razorpay=RazorpayConfig(key_id="rzp_test_xxx", key_secret="s"),
        discord=DiscordConfig(
            bot_token="token",
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
        catalog_path=catalog_path,
    )


@pytest.fixture()
def config(tmp_path: Path) -> Settings:
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(CATALOG_YAML)
    import openstore.surfaces.catalog as catalog_mod

    catalog_mod.CATALOG_CACHE = None
    catalog_mod._CATALOG_BY_PATH.clear()
    return _settings(str(catalog))


@pytest.fixture()
def session(config: Settings):
    import openstore.core.database as db_mod

    db_mod._engine = None
    init_database(config)
    s = get_session(config)
    try:
        yield s
    finally:
        s.close()
        db_mod._engine = None


class TestCartResolvesAgainstCatalog:
    def test_matching_cart_resolves(self, config: Settings, session) -> None:
        assert (
            cart_resolves_against_catalog(config, merchant_id(config), [VANILLA], session) is True
        )

    def test_price_drift_fails_closed(self, config: Settings, session) -> None:
        """The property e6 exists to catch: the cart was snapshotted at a price
        the serving catalog no longer offers."""
        drifted = {**VANILLA, "unit_minor": 9900}
        assert (
            cart_resolves_against_catalog(config, merchant_id(config), [drifted], session) is False
        )

    def test_tag_drift_fails_closed(self, config: Settings, session) -> None:
        retagged = {**VANILLA, "tags": ["vegan"]}
        assert (
            cart_resolves_against_catalog(config, merchant_id(config), [retagged], session) is False
        )

    def test_sku_gone_from_catalog_fails_closed(self, config: Settings, session) -> None:
        gone = {**VANILLA, "sku": "gelato_delisted"}
        assert cart_resolves_against_catalog(config, merchant_id(config), [gone], session) is False

    def test_line_from_another_merchant_fails_closed(self, config: Settings, session) -> None:
        """A federated line this sidecar's catalog cannot speak for is not one
        it may attest to (S12)."""
        foreign = {**VANILLA, "merchant_id": "some-other-merchant"}
        assert (
            cart_resolves_against_catalog(config, merchant_id(config), [foreign], session) is False
        )

    def test_empty_cart_fails_closed(self, config: Settings, session) -> None:
        assert cart_resolves_against_catalog(config, merchant_id(config), [], session) is False

    def test_unreadable_catalog_fails_closed(self, session) -> None:
        """A catalog the sidecar cannot read attests nothing — it must not read
        as a passing proof (R0.5)."""
        blind = _settings(None)
        assert cart_resolves_against_catalog(blind, merchant_id(blind), [VANILLA], session) is False

    def test_tag_order_is_not_drift(self, config: Settings, session) -> None:
        reordered = {**VANILLA, "tags": ["gelato", "vegan"]}
        assert (
            cart_resolves_against_catalog(config, merchant_id(config), [reordered], session) is True
        )


def _released_checkout(config: Settings, session, *, items: list[dict]):
    checkout, _ = get_or_create_checkout(
        session=session,
        checkout_id="chk_e6",
        trace_id="trace_e6",
        client_id="oc_test",
        merchant_id=merchant_id(config),
        cart_hash="sha256:cart_e6",
        cart_version=1,
        amount_minor=15000,
        currency="INR",
        policy_id=None,
        policy_hash=None,
        aal_level=2,
        expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=15),
        idempotency_key="idem_e6",
        cart_snapshot={"items": items},
    )
    create_reserve_entry(
        session=session,
        trace_id=checkout.trace_id,
        client_id=checkout.client_id,
        checkout_id=checkout.id,
        amount_minor=15000,
        currency="INR",
    )
    checkout.state = OrderState.HELD
    checkout.chat_platform = "web"
    checkout.chat_user_id = "buyerkey123456789012"
    checkout.chat_channel_id = "chan_1"
    checkout.request_text = "vegan gelato please"
    session.add(checkout)
    session.commit()
    session.refresh(checkout)
    return checkout


class TestBundleCarriesE6:
    def test_e6_holds_for_a_bundle_whose_cart_still_resolves(
        self, config: Settings, session
    ) -> None:
        checkout = _released_checkout(config, session, items=[VANILLA])

        psp_router_module._build_and_store_evidence(
            config, session, checkout, "Paid! ₹150.00 — order released."
        )
        session.commit()
        session.refresh(checkout)

        assert checkout.poai_bundle["goods"]["catalog_attestations_valid"] is True
        assert evaluate_aal_predicates(checkout.poai_bundle)["e6"] is True

    def test_e6_is_false_when_the_cart_no_longer_resolves(self, config: Settings, session) -> None:
        checkout = _released_checkout(config, session, items=[{**VANILLA, "unit_minor": 9900}])

        psp_router_module._build_and_store_evidence(
            config, session, checkout, "Paid! ₹99.00 — order released."
        )
        session.commit()
        session.refresh(checkout)

        assert checkout.poai_bundle["goods"]["catalog_attestations_valid"] is False
        assert evaluate_aal_predicates(checkout.poai_bundle)["e6"] is False
