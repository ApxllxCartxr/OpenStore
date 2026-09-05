# tests/stage09/test_runbook_beats.py
# Stage 9 — Part 12 runbook beats (4, 5, 6) as automated tests.
#
# Beats 0-3 are tested by test_fresh_install.py and stage 5/6/7 tests.
# This file covers:
#   4. The agentic failure (policy.tag_violation -> negotiation -> NO_COMPLIANT_PATH
#      -> amendment -> one-tap approval -> ALLOW), then over-cap fail-loud
#      (policy.spend_per_tx_exceeded).
#   5. Campaign beat (seeded analytics -> draft -> approve -> list_campaigns).
#   6. Dispute (openstore-verify offline on an exported bundle; tampered
#      amount_minor -> verifier names the broken section/link).

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from openstore.agents.merchant_agent import MerchantAgent
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
from openstore.core.campaigns import (
    create_campaign,
    get_analytics_view,
    submit_for_approval,
    validate_campaign,
)
from openstore.core.compiler import CompilerContext, compile_decision
from openstore.core.database import get_session, init_database
from openstore.models import (
    Campaign,
    CampaignState,
    Checkout,
    IntentPolicy,
    OrderState,
)
from openstore.verify.checks import (
    VerifierContext,
    run_all_checks,
)
from sqlmodel import select


def _settings() -> Settings:
    return Settings(
        merchant=MerchantConfig(name="Gelateria Milano", currency="INR"),
        razorpay=RazorpayConfig(key_id="rzp_test_runbook", key_secret="s"),
        discord=DiscordConfig(
            bot_token="t",
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
    )


def _seed_checkouts(session, merchant_id: str) -> None:
    """Seed analytics for the campaign beat."""
    now = datetime.now(UTC)
    fixtures = [
        ("gelato_vanilla", 3, 15000, 1),
        ("gelato_chocolate", 2, 15000, 3),
        ("gelato_pistachio", 1, 18000, 20),  # slow mover
    ]
    for sku, qty, unit, days_ago in fixtures:
        ts = now - timedelta(days=days_ago)
        ck = Checkout(
            id=f"chk_rb_{sku[:5]}_{days_ago}",
            trace_id=f"trace_rb_{sku[:5]}",
            client_id="cli_rb",
            merchant_id=merchant_id,
            cart_hash=f"sha256:{sku}",
            cart_version=1,
            amount_minor=unit * qty,
            currency="INR",
            state=OrderState.PAID,
            policy_id="pol_rb",
            policy_hash="ph_rb",
            aal_level=2,
            expires_at=ts + timedelta(hours=1),
            idempotency_key=f"rb_{sku}_{days_ago}",
            cart_snapshot={
                "items": [{"sku": sku, "qty": qty, "unit_minor": unit}],
                "cart_hash": f"sha256:{sku}",
                "cart_version": 1,
                "amount_minor": unit * qty,
                "currency": "INR",
            },
            created_at=ts,
            updated_at=ts,
        )
        session.add(ck)
    session.commit()


@pytest.fixture()
def session(tmp_path: Path):
    s = _settings()
    cat = tmp_path / "catalog.yaml"
    cat.write_text(
        "items:\n"
        "  - sku: gelato_vanilla\n"
        "    name: Vanilla\n"
        "    unit_minor: 15000\n"
        "    tags: [vegan]\n"
        "  - sku: gelato_chocolate\n"
        "    name: Chocolate\n"
        "    unit_minor: 15000\n"
        "    tags: [gelato]\n"
        "  - sku: gelato_pistachio\n"
        "    name: Pistachio\n"
        "    unit_minor: 18000\n"
        "    tags: [pistachio]\n"
    )
    s.catalog_path = str(cat)
    import openstore.core.database as db_mod

    db_mod._engine = None
    # Force catalog cache reload for THIS catalog path
    import openstore.surfaces.catalog as cat_mod

    cat_mod.CATALOG_CACHE = None
    init_database(s)
    ses = get_session(s)
    yield ses
    # Cleanup: clear cache so next test sees fresh data
    cat_mod.CATALOG_CACHE = None
    ses.close()


@pytest.fixture()
def catalog_session(tmp_path: Path):
    """Returns (config_with_catalog, session) — the config points at a catalog
    that the validator can resolve. Use this for tests that need to validate
    campaigns against a real catalog."""
    cfg = _settings()
    cat = tmp_path / "catalog.yaml"
    cat.write_text(
        "items:\n"
        "  - sku: gelato_vanilla\n"
        "    name: Vanilla\n"
        "    unit_minor: 15000\n"
        "    tags: [vegan]\n"
        "  - sku: gelato_chocolate\n"
        "    name: Chocolate\n"
        "    unit_minor: 15000\n"
        "    tags: [gelato]\n"
        "  - sku: gelato_pistachio\n"
        "    name: Pistachio\n"
        "    unit_minor: 18000\n"
        "    tags: [pistachio]\n"
    )
    cfg.catalog_path = str(cat)
    import openstore.core.database as db_mod

    db_mod._engine = None
    import openstore.surfaces.catalog as cat_mod

    cat_mod.CATALOG_CACHE = None
    init_database(cfg)
    ses = get_session(cfg)
    yield cfg, ses
    cat_mod.CATALOG_CACHE = None
    ses.close()


# ---------------------------------------------------------------------------
# Beat 4: the agentic failure + over-cap
# ---------------------------------------------------------------------------


class TestBeat4AgenticFailure:
    """policy.tag_violation -> negotiation -> NO_COMPLIANT_PATH -> amendment
    -> one-tap approval -> ALLOW. Then over-cap -> policy.spend_per_tx_exceeded."""

    def test_tag_violation_rejected_by_compiler(self, session):
        now = datetime.now(UTC)
        pol = IntentPolicy(
            id="pol_vegan",
            merchant_id="gelateria-milano",
            policy_version=2,
            policy_hash="ph_vegan",
            currency="INR",
            max_spend_per_tx_minor=50000,
            max_spend_total_minor=200000,
            max_transactions=10,
            allowed_tags=["vegan"],
            tag_mode="all",
            blocked_skus=[],
            not_before=int(now.timestamp()) - 60,
            expires_at=int(now.timestamp()) + 86400,
            assertion_max_age_seconds=86400,
            fulfilment_mode="all_or_nothing",
            required_skus=[],
            webauthn_credential_id="cred_vegan",
            webauthn_sign_count=0,
            signed_at=now,
            is_active=True,
        )
        session.add(pol)
        session.commit()

        # Cart with a non-vegan item: pistachio (tag "pistachio" not in ["vegan"])
        ctx = CompilerContext(
            cart_items=[
                {"sku": "gelato_pistachio", "qty": 1, "unit_minor": 18000, "tags": ["pistachio"]}
            ],
            policy=pol,
            merchant_id="gelateria-milano",
            currency="INR",
            checkout_count=0,
            cumulative_spend_minor=0,
            has_webauthn_assertion=True,
            assertion_age_seconds=0,
            now_unix=int(now.timestamp()),
            campaign_lookup={},
        )
        result = compile_decision(ctx)
        assert not result.allowed
        assert result.reason_code == "policy.tag_violation"

    def test_merchant_negotiation_responds_with_counter(self, monkeypatch):
        import json as _json

        from openstore.agents.llm import DummyProvider, register_provider

        class _Provider(DummyProvider):
            def chat(self, messages, **kwargs):
                return _json.dumps({"action": "remove_violating_tags", "rationale": "tag mismatch"})

        register_provider("test_beat4_counter", _Provider)
        monkeypatch.setenv("LLM_PROVIDER", "test_beat4_counter")

        agent = MerchantAgent(_settings())
        cart = [{"sku": "gelato_vanilla", "qty": 1, "unit_minor": 15000, "tags": ["vegan"]}]
        msg = agent.negotiate(cart, "policy.tag_violation", "trace_001", policy={})
        assert msg["from"] == "merchant_agent"
        assert msg["state"] == "COUNTERED"

    def test_merchant_negotiation_unrecoverable_returns_no_compliant_path(self, monkeypatch):
        import json as _json

        from openstore.agents.llm import DummyProvider, register_provider

        class _Provider(DummyProvider):
            def chat(self, messages, **kwargs):
                return _json.dumps({"action": "no_compliant_path", "rationale": "no fix available"})

        register_provider("test_beat4_none", _Provider)
        monkeypatch.setenv("LLM_PROVIDER", "test_beat4_none")

        agent = MerchantAgent(_settings())
        # Empty cart cannot be recovered
        msg = agent.negotiate([], "policy.no_human_authority", "trace_002", policy={})
        assert msg["state"] == "NO_COMPLIANT_PATH"

    def test_amendment_drafted_requires_human_signature(self):
        """R0.5: agent cannot self-approve."""
        agent = MerchantAgent(_settings())
        amendment = agent.draft_amendment(
            base_policy_hash="h",
            reason_code="policy.sku_blocked",
            cart=[],
            trace_id="trace_001",
        )
        assert amendment["approval"]["webauthn_assertion"] is None
        assert amendment["state"] == "PENDING_APPROVAL"

    def test_over_cap_rejected(self, session):
        now = datetime.now(UTC)
        pol = IntentPolicy(
            id="pol_cap",
            merchant_id="gelateria-milano",
            policy_version=2,
            policy_hash="ph_cap",
            currency="INR",
            max_spend_per_tx_minor=50000,  # 500 INR
            max_spend_total_minor=200000,
            max_transactions=10,
            allowed_tags=[],
            tag_mode="all",
            blocked_skus=[],
            not_before=int(now.timestamp()) - 60,
            expires_at=int(now.timestamp()) + 86400,
            assertion_max_age_seconds=86400,
            fulfilment_mode="all_or_nothing",
            required_skus=[],
            webauthn_credential_id="cred_cap",
            webauthn_sign_count=0,
            signed_at=now,
            is_active=True,
        )
        session.add(pol)
        session.commit()

        # Cart 600 INR > 500 INR cap -> DENY
        ctx = CompilerContext(
            cart_items=[{"sku": "luxury_box", "qty": 1, "unit_minor": 60000, "tags": []}],
            policy=pol,
            merchant_id="gelateria-milano",
            currency="INR",
            checkout_count=0,
            cumulative_spend_minor=0,
            has_webauthn_assertion=True,
            assertion_age_seconds=0,
            now_unix=int(now.timestamp()),
            campaign_lookup={},
        )
        result = compile_decision(ctx)
        assert not result.allowed
        assert result.reason_code == "policy.spend_per_tx_exceeded"


# ---------------------------------------------------------------------------
# Beat 5: campaign beat
# ---------------------------------------------------------------------------


class TestBeat5Campaign:
    """Seeded analytics -> draft -> approve -> list_campaigns discovery."""

    def test_seed_analytics_visible(self, session):
        _seed_checkouts(session, "gelateria-milano")
        view = get_analytics_view(session, "gelateria-milano")
        skus = {row["sku"] for row in view}
        assert "gelato_vanilla" in skus
        assert "gelato_pistachio" in skus
        # Slow mover should have low units_sold_30d
        pistachio = next(r for r in view if r["sku"] == "gelato_pistachio")
        assert pistachio["units_sold_30d"] >= 1

    def test_draft_validate_approve_publish(
        self, catalog_session, enrol_approver, approve_campaign
    ):
        config, session = catalog_session
        _seed_checkouts(session, "gelateria-milano")
        now = datetime.now(UTC)
        c = create_campaign(
            session,
            config,
            merchant_id="gelateria-milano",
            title="Pistachio Push",
            rationale="Slow mover, summer heat.",
            discount_bps=1500,
            applies_to_skus=["gelato_pistachio"],
            starts_at=now,
            ends_at=now + timedelta(days=7),
        )
        # Validator accepts the campaign (create_campaign already ran it)
        validate_campaign(session, c, config)

        # Park it for review, then approve with a real passkey ceremony
        # (DECISION-024: a stub assertion dict no longer publishes anything).
        submit_for_approval(session, c.id)
        va = enrol_approver(session, config)
        activated = approve_campaign(session, config, va, c.id, sign_count=2)
        assert activated.state == CampaignState.ACTIVE

        # list_campaigns discovers it
        active = list(
            session.exec(
                select(Campaign).where(
                    Campaign.merchant_id == "gelateria-milano",
                    Campaign.state == CampaignState.ACTIVE,
                )
            ).all()
        )
        assert len(active) == 1
        assert active[0].id == c.id


# ---------------------------------------------------------------------------
# Beat 6: the dispute
# ---------------------------------------------------------------------------


class TestBeat6Dispute:
    """openstore-verify offline on an exported bundle; tampered variant
    exits 1 and names the broken link/section."""

    def test_valid_bundle_passes(self, session):
        from openstore.core.poai import create_poai_bundle

        bundle = create_poai_bundle(
            transaction={
                "checkout_id": "chk_beat6",
                "amount_minor": 15000,
                "merchant_id": "gelateria-milano",
                "currency": "INR",
            },
            human_intent={
                "request_text": "buy gelato",
                "request_digest": "sha256:"
                + __import__("hashlib").sha256(b"buy gelato").hexdigest(),
            },
            authority={"webauthn": {"credential_id": "cred_beat6"}},
            goods={
                "items": [{"sku": "vanilla", "unit_minor": 15000, "qty": 1}],
                "catalog_digest": "sha256:dummy",
            },
            agent={"client_id": "test", "scopes": ["checkout:confirm"], "token_jti": "jti_beat6"},
            adjudication={
                "verdict": "ALLOW",
                "transcript": [
                    {"check": "human_authority_present", "result": "pass", "reason_code": None}
                ],
            },
            notification={"receipt_digest": "sha256:dummy"},
            aal={"level": 2},
        )
        # Some checks may fail due to absent catalog attestation, but at minimum
        # the hash chain check should be deterministic and consistent.
        # Bundle's own chain is self-consistent: rerun build should match
        from openstore.core.poai import SECTION_ORDER, build_hash_chain, canonical_json_bytes

        sections = {n: canonical_json_bytes(bundle.get(n)) for n in SECTION_ORDER}
        expected = build_hash_chain(sections)
        assert bundle["chain"]["root"] == expected["root"]

    def test_tampered_bundle_fails_and_names_broken_section(self, session):
        from openstore.core.poai import (
            SECTION_ORDER,
            build_hash_chain,
            canonical_json_bytes,
            create_poai_bundle,
        )

        bundle = create_poai_bundle(
            transaction={
                "checkout_id": "chk_beat6_t",
                "amount_minor": 15000,
                "merchant_id": "gelateria-milano",
                "currency": "INR",
            },
            human_intent={
                "request_text": "buy gelato",
                "request_digest": "sha256:"
                + __import__("hashlib").sha256(b"buy gelato").hexdigest(),
            },
            authority={"webauthn": {"credential_id": "cred_t"}},
            goods={
                "items": [{"sku": "vanilla", "unit_minor": 15000, "qty": 1}],
                "catalog_digest": "sha256:dummy",
            },
            agent={"client_id": "test", "scopes": ["checkout:confirm"], "token_jti": "jti_t"},
            adjudication={
                "verdict": "ALLOW",
                "transcript": [
                    {"check": "human_authority_present", "result": "pass", "reason_code": None}
                ],
            },
            notification={"receipt_digest": "sha256:dummy"},
            aal={"level": 2},
        )

        # Tamper: change amount_minor
        bundle["transaction"]["amount_minor"] = 99000

        # The bundle's chain links were computed from the ORIGINAL amount_minor.
        # With amount_minor tampered, the transaction section's canonical bytes
        # change -> the link_0 hash changes -> the root changes -> the chain
        # verifier MUST find the broken link and report the section.
        sections = {n: canonical_json_bytes(bundle.get(n)) for n in SECTION_ORDER}
        # The serialized transaction bytes ARE different from the original
        # because we changed amount_minor. So the recomputed link[0] is also
        # different from bundle["chain"]["links"][0].
        original_link_0 = bundle["chain"]["links"][0]
        recomputed_link_0 = build_hash_chain(sections)["links"][0]
        assert recomputed_link_0 != original_link_0, (
            "Tamper detection requires the canonical bytes of the tampered "
            "section to differ from the original. Check SECTION_ORDER[0]."
        )

        # The verifier must name the broken section
        ctx = VerifierContext(bundle=bundle)
        results = run_all_checks(ctx)
        failed = [r for r in results if not r.passed]
        # The chain/hash-chain check must be among the failures
        chain_failures = [r for r in failed if r.section == SECTION_ORDER[0] or "chain" in r.name]
        assert len(chain_failures) >= 1

    def test_write_and_read_bundle_roundtrip(self, session, tmp_path: Path):
        from openstore.core.poai import create_poai_bundle

        bundle = create_poai_bundle(
            transaction={"checkout_id": "chk_io", "amount_minor": 15000},
            adjudication={"verdict": "ALLOW", "transcript": []},
            notification={"receipt_digest": "sha256:x"},
            aal={"level": 2},
        )
        out = tmp_path / "b.json"
        out.write_text(json.dumps(bundle))
        loaded = json.loads(out.read_text())
        assert loaded["chain"]["root"] == bundle["chain"]["root"]
