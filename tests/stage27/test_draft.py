# tests/stage27/test_draft.py
# Stage 27.5: draft_merchandising_rules reads ONLY aggregates
# (get_analytics_view + co-occurrence counts — INV-14: no raw orders, buyer
# identities, or payment data), drafts deterministically, and never activates.

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import MERCHANT
from openstore.core.database import get_or_create_checkout
from openstore.core.merchandising import (
    co_occurrence_counts,
    draft_merchandising_rules,
)
from openstore.models import CampaignState, MerchandisingKind

PAIR = ("gelato_vanilla", "cone_waffle")


def _seed_pair(session, n: int, skus: tuple[str, str] = PAIR) -> None:
    created = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=20)
    for i in range(n):
        checkout, _ = get_or_create_checkout(
            session=session,
            checkout_id=f"chk_co_{skus[0]}_{i}",
            trace_id=f"trace_co_{i}",
            client_id="oc_test",
            merchant_id=MERCHANT,
            cart_hash=f"cart_co_{i}",
            cart_version=1,
            amount_minor=18000,
            currency="INR",
            policy_id=None,
            policy_hash=None,
            aal_level=2,
            expires_at=created + timedelta(hours=1),
            idempotency_key=f"idem_co_{i}",
            cart_snapshot={
                "items": [
                    {"sku": skus[0], "qty": 1, "unit_minor": 15000},
                    {"sku": skus[1], "qty": 1, "unit_minor": 3000},
                ]
            },
            agent_plan=None,
        )
        checkout.created_at = created
        session.add(checkout)
    session.commit()


def test_co_occurrence_counts_pairs_only(session):
    _seed_pair(session, 3)
    counts = co_occurrence_counts(session, MERCHANT)
    assert counts == {tuple(sorted(PAIR)): 3}


def test_drafts_from_aggregates(session, config):
    _seed_pair(session, 3)
    drafts = draft_merchandising_rules(session, config, MERCHANT)
    assert drafts, "three joint checkouts must propose at least one rule"
    for draft in drafts:
        assert draft.state == CampaignState.DRAFT
        assert draft.kind == MerchandisingKind.CROSS_SELL
        assert set(draft.trigger_skus) | {draft.suggested_sku} <= set(PAIR)
        # INV-14: signals are counts with a trigger label — no buyer identity,
        # no raw orders, no payment data anywhere in the draft inputs.
        assert set(draft.source_signals) <= {"trigger", "together_count"}
    session.commit()


def test_drafts_deterministic_and_single_pair_only(session, config):
    _seed_pair(session, 3)
    first = [(d.trigger_skus, d.suggested_sku) for d in
             draft_merchandising_rules(session, config, MERCHANT)]
    session.rollback()
    second = [(d.trigger_skus, d.suggested_sku) for d in
              draft_merchandising_rules(session, config, MERCHANT)]
    assert first == second
    assert len(first) <= 2  # at most one rule per ordered direction


def test_no_co_occurrence_no_drafts(session, config):
    assert draft_merchandising_rules(session, config, MERCHANT) == []


def test_single_joint_checkout_below_threshold(session, config):
    _seed_pair(session, 1)
    assert draft_merchandising_rules(session, config, MERCHANT) == []
