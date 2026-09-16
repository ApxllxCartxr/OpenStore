# tests/stage27/test_bundle.py
# Stage 27 (DECISION-048): a BUNDLE rule references a Campaign for its
# discount — exactly one discount path, recomputed by compiler check 12 and
# present in evidence. A paused/expired bundle is silently inapplicable.

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import MERCHANT
from openstore.core.campaigns import (
    create_campaign,
    pause_campaign,
)
from openstore.core.campaigns import (
    submit_for_approval as submit_campaign,
)
from openstore.core.compiler import CompilerContext, compile_decision
from openstore.core.merchandising import (
    create_rule,
    submit_for_approval,
    suggest_for_cart,
)
from openstore.models import CampaignState, IntentPolicy, MerchandisingKind


def _policy() -> IntentPolicy:
    now = datetime.now(UTC)
    return IntentPolicy(
        id="pol_bundle", merchant_id=MERCHANT, policy_version=2, policy_hash="ph",
        currency="INR", max_spend_per_tx_minor=10_000_000,
        max_spend_total_minor=100_000_000, max_transactions=100,
        allowed_tags=[], tag_mode="all", blocked_skus=[],
        not_before=int(now.timestamp()) - 60,
        expires_at=int(now.timestamp()) + 86400,
        assertion_max_age_seconds=86400, no_human_authority=True,
        fulfilment_mode="all_or_nothing", required_skus=[],
        webauthn_credential_id="cred", webauthn_sign_count=0,
        signed_at=now.replace(tzinfo=None), is_active=True,
    )


def _live_campaign(session, config, approve_campaign, enrol_approver):
    now = datetime.now(UTC).replace(tzinfo=None)
    va = enrol_approver(session, config)
    campaign = create_campaign(
        session, config, MERCHANT,
        title="Vanilla bundle", rationale="R", discount_bps=1000,
        applies_to_skus=["gelato_vanilla"],
        starts_at=now - timedelta(hours=1), ends_at=now + timedelta(days=7),
    )
    submit_campaign(session, campaign.id)
    session.commit()
    approve_campaign(session, config, va, campaign.id, sign_count=2)
    session.commit()
    return campaign


def _bundle_rule(session, config, campaign_id: str):
    rule = create_rule(
        session, config, MERCHANT, MerchandisingKind.BUNDLE,
        "Vanilla + cone bundle", "R",
        ["gelato_vanilla"], "cone_waffle", campaign_id=campaign_id,
    )
    submit_for_approval(session, rule.id)
    session.commit()
    from openstore.models import MerchandisingRule
    from sqlmodel import select

    row = session.exec(
        select(MerchandisingRule).where(MerchandisingRule.id == rule.id)
    ).first()
    assert row is not None
    row.state = CampaignState.ACTIVE
    session.add(row)
    session.commit()
    return rule


def test_bundle_suggests_with_campaign_terms(
    session, config, approve_campaign, enrol_approver
):
    campaign = _live_campaign(session, config, approve_campaign, enrol_approver)
    _bundle_rule(session, config, campaign.id)
    got = suggest_for_cart(session, config, MERCHANT, ["gelato_vanilla"])
    bundles = [s for s in got if s.get("kind") == "BUNDLE"]
    assert len(bundles) == 1
    assert bundles[0]["campaign_id"] == campaign.id
    assert bundles[0]["discount_bps"] == 1000


def test_bundle_discount_recomputed_by_check_12(
    session, config, approve_campaign, enrol_approver
):
    """The bundle discount exists iff check 12 recomputes it — the same
    campaign_lookup path every campaign-discounted cart takes (R0.8)."""
    campaign = _live_campaign(session, config, approve_campaign, enrol_approver)
    _bundle_rule(session, config, campaign.id)
    now = datetime.now(UTC)
    ctx = CompilerContext(
        cart_items=[{
            "sku": "gelato_vanilla", "qty": 1, "unit_minor": 15000,
            "tags": ["vegan", "gelato"], "campaign_id": campaign.id,
        }],
        policy=_policy(),
        merchant_id=MERCHANT,
        currency="INR",
        checkout_count=0,
        cumulative_spend_minor=0,
        has_webauthn_assertion=True,
        assertion_age_seconds=0,
        now_unix=int(now.timestamp()),
        campaign_lookup={
            campaign.id: {
                "id": campaign.id,
                "state": "ACTIVE",
                "offer_terms": {
                    "discount_bps": 1000,
                    "applies_to_skus": ["gelato_vanilla"],
                    "starts_at": campaign.starts_at.isoformat() + "Z",
                    "ends_at": campaign.ends_at.isoformat() + "Z",
                },
            }
        },
    )
    result = compile_decision(ctx)
    assert result.allowed is True
    assert result.effective_amount_minor == 15000 - 1500


def test_paused_bundle_silently_inapplicable(
    session, config, approve_campaign, enrol_approver
):
    campaign = _live_campaign(session, config, approve_campaign, enrol_approver)
    _bundle_rule(session, config, campaign.id)
    pause_campaign(session, campaign.id)
    session.commit()
    got = suggest_for_cart(session, config, MERCHANT, ["gelato_vanilla"])
    assert [s for s in got if s.get("kind") == "BUNDLE"] == []
    # …while the underlying related fallback still answers.
    assert [s["sku"] for s in got] == ["cone_waffle"]
