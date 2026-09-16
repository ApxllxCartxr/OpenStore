# tests/stage27/test_rule_lifecycle.py
# Stage 27.1: merchandising_rules mirrors Campaign's state machine; unknown
# SKUs fail loud; BUNDLE needs a live campaign reference at draft time.

from __future__ import annotations

import pytest
from conftest import MERCHANT
from openstore.core.merchandising import (
    MerchandisingError,
    activate_rule,
    create_rule,
    pause_rule,
    reject_rule,
    submit_for_approval,
)
from openstore.models import CampaignState, MerchandisingKind


def _draft(session, config, **kw):
    args = {
        "kind": MerchandisingKind.CROSS_SELL,
        "title": "Goes with vanilla",
        "rationale": "test draft",
        "trigger_skus": ["gelato_vanilla"],
        "suggested_sku": "cone_waffle",
    }
    args.update(kw)
    return create_rule(session, config, MERCHANT, **args)


def test_draft_submit_lifecycle(session, config):
    rule = _draft(session, config)
    assert rule.state == CampaignState.DRAFT
    session.commit()
    rule = submit_for_approval(session, rule.id)
    assert rule.state == CampaignState.PENDING_APPROVAL
    session.commit()
    rule = reject_rule(session, rule.id, "not this season")
    assert rule.state == CampaignState.REJECTED


def test_out_of_order_transition_fails(session, config):
    rule = _draft(session, config)
    session.commit()
    with pytest.raises(MerchandisingError) as exc:
        pause_rule(session, rule.id)
    assert exc.value.reason_code == "merchandising.invalid_state_transition"
    with pytest.raises(MerchandisingError) as exc:
        activate_rule(
            session, rule.id, "cred",
            webauthn_assertion={"credential_id": "c"},
            config=config, user_handle="op",
        )
    assert exc.value.reason_code == "merchandising.invalid_state_transition"


def test_unknown_rule_fails(session, config):
    with pytest.raises(MerchandisingError) as exc:
        submit_for_approval(session, "mrule_nope")
    assert exc.value.reason_code == "merchandising.not_found"


def test_unknown_sku_fails_loud(session, config):
    with pytest.raises(MerchandisingError) as exc:
        _draft(session, config, suggested_sku="hallucinated_sku")
    assert exc.value.reason_code == "catalog.sku_not_found"


def test_self_trigger_and_empty_rejected(session, config):
    with pytest.raises(MerchandisingError) as exc:
        _draft(session, config, trigger_skus=["cone_waffle"], suggested_sku="cone_waffle")
    assert exc.value.reason_code == "merchandising.invalid_rule"
    with pytest.raises(MerchandisingError) as exc:
        _draft(session, config, trigger_skus=[])
    assert exc.value.reason_code == "merchandising.invalid_rule"


def test_bundle_needs_campaign(session, config):
    with pytest.raises(MerchandisingError) as exc:
        _draft(session, config, kind=MerchandisingKind.BUNDLE, campaign_id=None)
    assert exc.value.reason_code == "merchandising.invalid_rule"
    with pytest.raises(MerchandisingError) as exc:
        _draft(session, config, kind=MerchandisingKind.BUNDLE, campaign_id="camp_nope")
    assert exc.value.reason_code == "campaign.not_found"
    with pytest.raises(MerchandisingError) as exc:
        _draft(session, config, kind=MerchandisingKind.CROSS_SELL, campaign_id="camp_x")
    assert exc.value.reason_code == "merchandising.invalid_rule"


def test_activation_needs_assertion(session, config):
    rule = _draft(session, config)
    session.commit()
    submit_for_approval(session, rule.id)
    session.commit()
    with pytest.raises(MerchandisingError) as exc:
        activate_rule(session, rule.id, "cred", webauthn_assertion=None,
                      config=config, user_handle="op")
    assert exc.value.reason_code == "merchandising.no_webauthn_approval"
