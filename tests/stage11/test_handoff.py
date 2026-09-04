# tests/stage11/test_handoff.py
# S11 Phase 2 (Q-014/Q-016): the handoff bridge — create/resolve/consume/expire,
# and the credential-join policy check that gates a chat errand on a signed
# policy (authority.policy_unsigned).

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from openstore.core.handoff import (
    HandoffError,
    buyer_handle,
    consume_handoff,
    create_handoff,
    require_active_policy,
    resolve_handoff,
)
from openstore.models import HandoffKind, IntentPolicy, WebAuthnCredential


def _make_handoff(session, **overrides):
    defaults: dict = dict(
        kind=HandoffKind.POLICY,
        merchant_id="gelateria",
        chat_platform="discord",
        chat_user_id="123",
        chat_channel_id="456",
        request_text="vegan gelato please",
    )
    defaults.update(overrides)
    handoff = create_handoff(session, **defaults)
    session.commit()
    return handoff


class TestHandoffLifecycle:
    def test_create_resolve_consume(self, session):
        handoff = _make_handoff(session)
        assert buyer_handle(handoff) == "discord:123"

        resolved = resolve_handoff(session, handoff.token)
        assert resolved.token == handoff.token
        assert resolved.consumed_at is None

        consumed = consume_handoff(session, handoff.token, result_policy_id="pol_abc")
        session.commit()
        assert consumed.consumed_at is not None
        assert consumed.result_policy_id == "pol_abc"

    def test_double_consume_rejected(self, session):
        handoff = _make_handoff(session)
        consume_handoff(session, handoff.token)
        session.commit()
        with pytest.raises(HandoffError) as exc:
            consume_handoff(session, handoff.token)
        assert exc.value.reason_code == "authority.handoff_consumed"

    def test_unknown_token_rejected(self, session):
        with pytest.raises(HandoffError) as exc:
            resolve_handoff(session, "does-not-exist")
        assert exc.value.reason_code == "authority.handoff_not_found"

    def test_expired_token_rejected(self, session):
        handoff = _make_handoff(session, ttl_seconds=-10)
        with pytest.raises(HandoffError) as exc:
            resolve_handoff(session, handoff.token)
        assert exc.value.reason_code == "authority.handoff_expired"

    def test_kind_amendment_round_trips(self, session):
        handoff = _make_handoff(session, kind=HandoffKind.AMENDMENT, chat_user_id="999")
        resolved = resolve_handoff(session, handoff.token)
        assert resolved.kind == HandoffKind.AMENDMENT


class TestRequireActivePolicy:
    def test_no_credentials_raises_policy_unsigned(self, session):
        with pytest.raises(HandoffError) as exc:
            require_active_policy(session, "discord:999")
        assert exc.value.reason_code == "authority.policy_unsigned"

    def test_active_policy_found_via_credential_join(self, session):
        cred = WebAuthnCredential(
            credential_id="cred_handoff_1",
            user_handle="discord:42",
            public_key=b"\x00" * 32,
            sign_count=1,
        )
        session.add(cred)
        session.flush()

        now = int(datetime.now(UTC).timestamp())
        policy = IntentPolicy(
            id="pol_handoff_1",
            merchant_id="gelateria",
            policy_hash="h" * 64,
            max_spend_per_tx_minor=1000,
            max_spend_total_minor=5000,
            max_transactions=5,
            allowed_tags=[],
            blocked_skus=[],
            required_skus=[],
            not_before=now - 10,
            expires_at=now + 3600,
            webauthn_credential_id="cred_handoff_1",
            webauthn_sign_count=1,
            signed_at=datetime.now(UTC).replace(tzinfo=None),
        )
        session.add(policy)
        session.commit()

        resolved = require_active_policy(session, "discord:42")
        assert resolved.id == "pol_handoff_1"

    def test_expired_policy_does_not_satisfy_the_check(self, session):
        cred = WebAuthnCredential(
            credential_id="cred_handoff_2",
            user_handle="discord:43",
            public_key=b"\x00" * 32,
            sign_count=1,
        )
        session.add(cred)
        session.flush()

        now = int(datetime.now(UTC).timestamp())
        policy = IntentPolicy(
            id="pol_handoff_2",
            merchant_id="gelateria",
            policy_hash="h" * 64,
            max_spend_per_tx_minor=1000,
            max_spend_total_minor=5000,
            max_transactions=5,
            allowed_tags=[],
            blocked_skus=[],
            required_skus=[],
            not_before=now - 3600,
            expires_at=now - 10,  # already expired
            webauthn_credential_id="cred_handoff_2",
            webauthn_sign_count=1,
            signed_at=datetime.now(UTC).replace(tzinfo=None),
        )
        session.add(policy)
        session.commit()

        with pytest.raises(HandoffError) as exc:
            require_active_policy(session, "discord:43")
        assert exc.value.reason_code == "authority.policy_unsigned"
