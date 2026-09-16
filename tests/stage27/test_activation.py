# tests/stage27/test_activation.py
# Stage 27.4: a draft activates solely on a passkey assertion bound to
# {"mode": "merchandising", "rule_id": ...}; a cross-rule replay fails closed.
# Genuine ceremonies via the shared VirtualAuthenticator fixtures (the same
# shape as the campaign approval tests).

from __future__ import annotations

import pytest
from conftest import MERCHANT
from openstore.core.merchandising import (
    MerchandisingError,
    activate_rule,
    create_rule,
    pause_rule,
    submit_for_approval,
)
from openstore.models import CampaignState, MerchandisingKind


def _draft(session, config, **kw):
    args = {
        "kind": MerchandisingKind.CROSS_SELL,
        "title": "T",
        "rationale": "R",
        "trigger_skus": ["gelato_vanilla"],
        "suggested_sku": "cone_waffle",
    }
    args.update(kw)
    rule = create_rule(session, config, MERCHANT, **args)
    session.commit()
    return rule


def _approve(session, config, va, rule_id: str, *, sign_count: int = 2, bind_to: str | None = None,
             user_handle: str = MERCHANT):
    """Genuine approval ceremony, mirroring tests/conftest.py approve_campaign
    with the merchandising binding. bind_to issues the challenge for a
    DIFFERENT rule than the one being approved (replay)."""
    from openstore.core.webauthn_rp import begin_assertion
    from openstore.devtools.virtual_authenticator import b64u_raw

    begin = begin_assertion(
        config,
        user_handle,
        binding={"mode": "merchandising", "rule_id": bind_to or rule_id},
    )
    asr = va.assert_credential(b64u_raw(begin["challenge"]), sign_count=sign_count)
    return activate_rule(
        session,
        rule_id,
        approver_credential_id=asr.credential_id,
        webauthn_assertion={
            "credential_id": asr.credential_id,
            "client_data_json": asr.client_data_json,
            "authenticator_data": asr.authenticator_data,
            "signature": asr.signature,
            "challenge": begin["challenge"],
        },
        config=config,
        user_handle=user_handle,
    )


def test_activate_succeeds(session, config, enrol_approver):
    va = enrol_approver(session, config)
    rule = _draft(session, config)
    submit_for_approval(session, rule.id)
    session.commit()
    activated = _approve(session, config, va, rule.id, sign_count=2)
    assert activated.state == CampaignState.ACTIVE
    assert activated.approver_credential_id is not None
    assert activated.webauthn_assertion is not None
    assert activated.approved_at is not None


def test_cross_rule_replay_fails_closed(session, config, enrol_approver):
    """An assertion approving rule B cannot activate rule A — the binding
    carries the rule_id, so verification fails and A stays PENDING."""
    va = enrol_approver(session, config)
    rule_a = _draft(session, config)
    rule_b = _draft(
        session, config, trigger_skus=["gelato_pistachio"], suggested_sku="cone_waffle"
    )
    submit_for_approval(session, rule_a.id)
    submit_for_approval(session, rule_b.id)
    session.commit()
    with pytest.raises(MerchandisingError) as exc:
        _approve(session, config, va, rule_a.id, sign_count=2, bind_to=rule_b.id)
    assert exc.value.reason_code == "merchandising.webauthn_verification_failed"
    session.rollback()
    from openstore.models import MerchandisingRule
    from sqlmodel import select

    fresh = session.exec(
        select(MerchandisingRule).where(MerchandisingRule.id == rule_a.id)
    ).first()
    assert fresh is not None and fresh.state == CampaignState.PENDING_APPROVAL


def test_pause_and_reject(session, config, enrol_approver):
    from openstore.core.merchandising import reject_rule

    va = enrol_approver(session, config)
    rule = _draft(session, config)
    submit_for_approval(session, rule.id)
    session.commit()
    _approve(session, config, va, rule.id, sign_count=2)
    session.commit()
    paused = pause_rule(session, rule.id)
    assert paused.state == CampaignState.PAUSED
    session.commit()
    with pytest.raises(MerchandisingError) as exc:
        reject_rule(session, rule.id)
    assert exc.value.reason_code == "merchandising.invalid_state_transition"
