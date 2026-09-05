# tests/stage11/test_mcp_policy_tools.py
# S11: resolve_policy / create_policy_handoff MCP tools — thin adapters over
# core/handoff.py so a remote buyer process can resolve/bootstrap its
# per-merchant intent policy over MCP instead of importing core/handoff.py
# directly (buyer_agent.py._handle_shop does the direct-import version today).

from __future__ import annotations

from datetime import UTC, datetime

from openstore.models import Handoff, HandoffKind, IntentPolicy, WebAuthnCredential
from openstore.surfaces import mcp_server


def _make_policy(session, *, user_handle: str, credential_id: str, policy_id: str) -> IntentPolicy:
    cred = WebAuthnCredential(
        credential_id=credential_id,
        user_handle=user_handle,
        public_key=b"\x00" * 32,
        sign_count=1,
    )
    session.add(cred)
    session.flush()

    now = int(datetime.now(UTC).timestamp())
    policy = IntentPolicy(
        id=policy_id,
        merchant_id="gelateria",
        policy_hash="h" * 64,
        max_spend_per_tx_minor=1000,
        max_spend_total_minor=5000,
        max_transactions=5,
        allowed_tags=["food"],
        tag_mode="any",
        blocked_skus=["sku_blocked"],
        required_skus=[],
        not_before=now - 10,
        expires_at=now + 3600,
        webauthn_credential_id=credential_id,
        webauthn_sign_count=1,
        signed_at=datetime.now(UTC).replace(tzinfo=None),
    )
    session.add(policy)
    session.commit()
    return policy


class TestResolvePolicy:
    def test_happy_path_returns_load_policy_fields(self, settings, session):
        _make_policy(
            session,
            user_handle="discord:mcp_res_1",
            credential_id="cred_res_1",
            policy_id="pol_res_1",
        )

        result = mcp_server.resolve_policy(
            settings, session, user_id="discord:mcp_res_1", token_scopes=["catalog:read"]
        )

        assert result.success is True
        assert result.data["policy_id"] == "pol_res_1"
        assert result.data["allowed_tags"] == ["food"]
        assert result.data["tag_mode"] == "any"
        assert result.data["blocked_skus"] == ["sku_blocked"]
        assert result.data["max_spend_per_tx_minor"] == 1000
        assert result.data["policy_hash"] == "h" * 64

    def test_no_active_policy_returns_policy_unsigned(self, settings, session):
        result = mcp_server.resolve_policy(
            settings, session, user_id="discord:mcp_res_nopolicy", token_scopes=["catalog:read"]
        )

        assert result.success is False
        assert result.error["reason_code"] == "authority.policy_unsigned"

    def test_missing_scope_is_rejected(self, settings, session):
        from openstore.core.api import CommerceError

        try:
            mcp_server.resolve_policy(
                settings, session, user_id="discord:mcp_res_1", token_scopes=[]
            )
            raise AssertionError("expected CommerceError")
        except CommerceError as e:
            assert e.reason_code == "auth.insufficient_scope"


class TestCreatePolicyHandoff:
    def test_happy_path_creates_handoff(self, settings, session):
        result = mcp_server.create_policy_handoff(
            settings,
            session,
            merchant_id="gelateria",
            chat_platform="discord",
            chat_user_id="123",
            chat_channel_id="456",
            request_text="vegan gelato please",
            token_scopes=["catalog:read"],
        )

        assert result.success is True
        token = result.data["token"]
        assert token

        stored = session.get(Handoff, token)
        assert stored is not None
        assert stored.kind == HandoffKind.POLICY
        assert stored.merchant_id == "gelateria"
        assert stored.chat_user_id == "123"
        assert stored.consumed_at is None

    def test_missing_scope_is_rejected(self, settings, session):
        from openstore.core.api import CommerceError

        try:
            mcp_server.create_policy_handoff(
                settings,
                session,
                merchant_id="gelateria",
                chat_platform="discord",
                chat_user_id="123",
                chat_channel_id="456",
                request_text="vegan gelato please",
                token_scopes=[],
            )
            raise AssertionError("expected CommerceError")
        except CommerceError as e:
            assert e.reason_code == "auth.insufficient_scope"

    def test_no_active_policy_path_bootstraps_via_handoff(self, settings, session):
        """Round-trip matching buyer_agent._handle_shop's existing flow: resolve_policy
        fails closed, and create_policy_handoff produces the signing-link token."""
        resolved = mcp_server.resolve_policy(
            settings, session, user_id="discord:mcp_bootstrap_1", token_scopes=["catalog:read"]
        )
        assert resolved.success is False
        assert resolved.error["reason_code"] == "authority.policy_unsigned"

        created = mcp_server.create_policy_handoff(
            settings,
            session,
            merchant_id="gelateria",
            chat_platform="discord",
            chat_user_id="777",
            chat_channel_id="456",
            request_text="vegan gelato please",
            token_scopes=["catalog:read"],
        )
        assert created.success is True
        assert created.data["token"]
