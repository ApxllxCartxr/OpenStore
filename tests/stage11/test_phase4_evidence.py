# tests/stage11/test_phase4_evidence.py
# S11 Phase 4: evidence bundles on completed purchases (plan items #20-22)
# and the two evidence routes (plan item #4).

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import openstore.core.database as _database_module
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openstore.core.database import (
    get_or_create_checkout,
    get_session,
    init_database,
    update_checkout_state,
)
from openstore.core.ledger import create_reserve_entry
from openstore.core.poai import evaluate_aal_predicates
from openstore.models import Checkout, OrderState
from openstore.psp import razorpay_driver as driver
from openstore.psp import router as psp_router_module
from openstore.surfaces.evidence import evidence_router

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "GOLDEN" / "razorpay"


@pytest.fixture()
def session(settings):
    """Same engine-reset isolation as test_phase3_money_chat.py's override —
    this file writes Checkout rows too."""
    _database_module._engine = None
    init_database(settings)
    s = get_session(settings)
    yield s
    s.close()
    _database_module._engine = None


def _make_held_checkout(
    session,
    *,
    checkout_id: str,
    amount_minor: int = 15000,
    request_text: str | None = "vegan gelato please",
    chat_user_id: str | None = "d77",
) -> Checkout:
    checkout, _ = get_or_create_checkout(
        session=session,
        checkout_id=checkout_id,
        trace_id=f"trace_{checkout_id}",
        client_id="oc_test",
        merchant_id="test-merchant",
        cart_hash=f"cart_{checkout_id}",
        cart_version=1,
        amount_minor=amount_minor,
        currency="INR",
        policy_id=None,
        policy_hash=None,
        aal_level=2,
        expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=15),
        idempotency_key=f"idem_{checkout_id}",
        cart_snapshot={"items": [{"sku": "vanilla", "qty": 1, "unit_minor": amount_minor}]},
    )
    create_reserve_entry(
        session=session,
        trace_id=checkout.trace_id,
        client_id=checkout.client_id,
        checkout_id=checkout.id,
        amount_minor=amount_minor,
        currency="INR",
    )
    update_checkout_state(session=session, checkout_id=checkout.id, new_state=OrderState.HELD)
    checkout.chat_platform = "discord"
    checkout.chat_user_id = chat_user_id
    checkout.chat_channel_id = "chan_1"
    checkout.request_text = request_text
    session.add(checkout)
    session.commit()
    session.refresh(checkout)
    return checkout


class TestBuildAndStoreEvidence:
    def test_predicates_e8_e9_hold_for_a_released_chat_checkout(self, settings, session):
        checkout = _make_held_checkout(session, checkout_id="chk_ev_direct")

        psp_router_module._build_and_store_evidence(
            settings, session, checkout, "Paid! ₹150.00 — order released."
        )
        session.commit()
        session.refresh(checkout)

        assert checkout.poai_bundle is not None
        predicates = evaluate_aal_predicates(checkout.poai_bundle)
        assert predicates["e8"] is True, "human_intent digest must match request_text"
        assert predicates["e9"] is True, "notification must carry a receipt_digest"

        # e8's digest is a raw sha256 hexdigest, NOT "sha256:"-prefixed
        # (poai.py's evaluate_aal_predicates compares against the raw form).
        import hashlib

        expected = hashlib.sha256(b"vegan gelato please").hexdigest()
        assert checkout.poai_bundle["human_intent"]["request_digest"] == expected

        # hash chain is internally consistent
        from openstore.core.poai import SECTION_ORDER, build_hash_chain, canonical_json_bytes

        sections = {n: canonical_json_bytes(checkout.poai_bundle.get(n)) for n in SECTION_ORDER}
        expected_chain = build_hash_chain(sections)
        assert checkout.poai_bundle["chain"]["root"] == expected_chain["root"]

    def test_no_bundle_without_request_text(self, settings, session):
        """A non-chat (or chat-with-no-goal-recorded) checkout gets no
        human_intent section — never a fabricated one (R0.3)."""
        checkout = _make_held_checkout(session, checkout_id="chk_ev_no_text", request_text=None)
        psp_router_module._build_and_store_evidence(settings, session, checkout, "Paid!")
        session.commit()
        session.refresh(checkout)
        assert checkout.poai_bundle["human_intent"] is None


class TestWebhookProducesEvidence:
    def test_payment_link_paid_persists_evidence_bundle(self, settings, session):
        checkout = _make_held_checkout(session, checkout_id="chk_ev_webhook", amount_minor=21000)

        payload = json.loads((GOLDEN_DIR / "payment_link_paid.json").read_text())
        entity = payload["payload"]["payment_link"]["entity"]
        entity["reference_id"] = checkout.id
        entity["notes"]["checkout_id"] = checkout.id
        entity["amount"] = checkout.amount_minor
        body = json.dumps(payload).encode()

        event = driver.persist_raw_webhook_event(
            session=session,
            raw_body=body,
            signature="test",
            x_event_id="evt_ev_webhook",
            trace_id="trace_ev_webhook",
            client_id="razorpay",
        )
        session.commit()

        psp_router_module._process_event_in_worker(event.psp_event_id, settings)

        session.refresh(checkout)
        assert checkout.state == OrderState.RELEASED
        assert checkout.poai_bundle is not None
        assert checkout.poai_bundle["transaction"]["checkout_id"] == checkout.id
        predicates = evaluate_aal_predicates(checkout.poai_bundle)
        assert predicates["e8"] is True
        assert predicates["e9"] is True


@pytest.fixture()
def evidence_client(settings, session) -> TestClient:
    app = FastAPI()
    app.include_router(evidence_router(settings, session_factory=lambda: get_session(settings)))
    with TestClient(app) as c:
        yield c


class TestEvidenceRoutes:
    def test_get_evidence_returns_persisted_bundle(self, settings, session, evidence_client):
        checkout = _make_held_checkout(session, checkout_id="chk_ev_route")
        psp_router_module._build_and_store_evidence(settings, session, checkout, "Paid!")
        session.commit()

        res = evidence_client.get(f"/orders/{checkout.id}/evidence")
        assert res.status_code == 200
        assert res.json()["bundle_id"] == checkout.poai_bundle["bundle_id"]

    def test_get_evidence_404_when_no_bundle_yet(self, settings, session, evidence_client):
        _make_held_checkout(session, checkout_id="chk_ev_pending")

        res = evidence_client.get("/orders/chk_ev_pending/evidence")
        assert res.status_code == 404
        assert res.json()["detail"]["reason_code"] == "checkout.evidence_not_found"

    def test_get_evidence_404_when_checkout_unknown(self, evidence_client):
        res = evidence_client.get("/orders/chk_does_not_exist/evidence")
        assert res.status_code == 404
        assert res.json()["detail"]["reason_code"] == "checkout.not_found"

    def test_view_renders_template_and_autoloads(self, settings, session, evidence_client):
        checkout = _make_held_checkout(session, checkout_id="chk_ev_view")
        psp_router_module._build_and_store_evidence(settings, session, checkout, "Paid!")
        session.commit()

        res = evidence_client.get(f"/orders/{checkout.id}/evidence/view")
        assert res.status_code == 200
        assert "PoAI Evidence Viewer" in res.text
        assert f"/orders/{checkout.id}/evidence" in res.text

    def test_view_404_when_checkout_unknown(self, evidence_client):
        res = evidence_client.get("/orders/chk_does_not_exist/evidence/view")
        assert res.status_code == 404
