# tests/stage25/test_adapter_sdk.py
# Stage 25 regression cover for defects found reviewing the adapter SDK:
# a second Session opened mid-transaction, a Protocol return type that was
# never constructible, and the legacy catalog_path dispatch path.

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from openstore.config import Settings
from openstore.core.campaigns import create_campaign
from openstore.core.database import get_or_create_checkout
from openstore.surfaces.adapters.base import AdapterHealth
from openstore.surfaces.catalog import load_catalog


class TestLegacyDispatch:
    def test_catalog_path_resolves_through_adapter(self, config: Settings) -> None:
        """Legacy catalog_path: normalizes into the yaml adapter untouched."""
        items = {i["sku"]: i for i in load_catalog(config)}
        assert set(items) == {"gelato_vanilla", "gelato_pistachio"}
        assert items["gelato_vanilla"]["unit_minor"] == 15000

    def test_normalized_shape_carries_stock_and_provenance(self, config: Settings) -> None:
        """The shape froze in Stage 25: unmanaged stock is None, never 0."""
        item = load_catalog(config)[0]
        assert item["stock"] is None
        assert item["related_source"] == "yaml"


class TestAdapterHealth:
    def test_yaml_health_check_returns_adapter_health(self, config: Settings) -> None:
        """health_check declared -> AdapterHealth; it used to call typing.Any,
        which raises TypeError on every invocation."""
        from openstore.surfaces.adapters.registry import get_adapter

        health = get_adapter(config, purpose="catalog").health_check()
        assert isinstance(health, AdapterHealth)
        assert health.ok is True
        assert health.item_count == 2


class TestOverlaySessionThreading:
    def test_campaign_validation_preserves_caller_transaction(
        self, session, config: Settings
    ) -> None:
        """validate_campaign loads the catalog, which resolves the merchant
        settings overlay. Resolving it through a second Session shares (and on
        close rolls back) the caller's connection under SQLite's StaticPool,
        silently discarding rows the caller had just written."""
        now = datetime.now(UTC).replace(tzinfo=None)
        get_or_create_checkout(
            session=session,
            checkout_id="chk_overlay",
            trace_id="trace_overlay",
            client_id="oc_test",
            merchant_id="gelateria-milano",
            cart_hash="cart_overlay",
            cart_version=1,
            amount_minor=15000,
            currency="INR",
            policy_id=None,
            policy_hash=None,
            aal_level=2,
            expires_at=now + timedelta(hours=1),
            idempotency_key="idem_overlay",
            cart_snapshot={"items": [{"sku": "gelato_vanilla", "qty": 1, "unit_minor": 15000}]},
        )

        create_campaign(
            session,
            config,
            merchant_id="gelateria-milano",
            title="Vanilla push",
            rationale="R",
            discount_bps=1000,
            applies_to_skus=["gelato_vanilla"],
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=6),
            source_signals={"trigger": "manual"},
        )

        from openstore.core.campaigns import get_analytics_view

        analytics = get_analytics_view(session, "gelateria-milano")
        assert [a["sku"] for a in analytics] == ["gelato_vanilla"], (
            "the seeded checkout was rolled back by a nested Session"
        )

    def test_unknown_sku_still_fails_loud(self, session, config: Settings) -> None:
        from openstore.core.campaigns import CampaignValidationError

        now = datetime.now(UTC).replace(tzinfo=None)
        with pytest.raises(CampaignValidationError) as ei:
            create_campaign(
                session,
                config,
                merchant_id="gelateria-milano",
                title="Ghost",
                rationale="R",
                discount_bps=1000,
                applies_to_skus=["not_a_sku"],
                starts_at=now - timedelta(days=1),
                ends_at=now + timedelta(days=6),
                source_signals={"trigger": "manual"},
            )
        assert ei.value.reason_code == "campaign.sku_not_found"
