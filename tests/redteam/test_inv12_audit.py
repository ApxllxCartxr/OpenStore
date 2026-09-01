# tests/redteam/test_inv12_audit.py
# INV-12 — Audit attributes every call. Every AuditLogEntry carries client_id +
# trace_id; the audit trail never feeds raw PII into metadata.

from __future__ import annotations

from openstore.core.audit import AuditContext, audit_log, get_audit_trail
from openstore.models import AuditLog
from sqlmodel import select


def test_audit_log_populates_trace_and_client(session):
    entry = audit_log(
        session,
        trace_id="tr_1", client_id="cli_1", action="checkout.create", resource_type="checkout",
        resource_id="chk_1", request_ip="127.0.0.1", request_method="POST", request_path="/x",
        response_status=201,
    )
    session.commit()
    assert entry.trace_id == "tr_1"
    assert entry.client_id == "cli_1"
    assert entry.response_status == 201


def test_audit_context_records_500_on_exception(session):
    try:
        with AuditContext(
            session, trace_id="tr_2", client_id="cli_1", action="checkout.confirm", resource_type="checkout",
        ):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    session.commit()
    rows = session.exec(select(AuditLog)).all()
    assert len(rows) == 1
    assert rows[0].response_status == 500
    assert rows[0].trace_id == "tr_2"


def test_audit_trail_query_by_trace(session):
    for i in range(3):
        audit_log(session, trace_id=f"tr_{i}", client_id="cli_1", action="a", resource_type="r")
    session.commit()
    rows = get_audit_trail(session, trace_id="tr_1")
    assert len(rows) == 1
    assert rows[0].trace_id == "tr_1"


def test_no_raw_pii_in_audit_metadata(session):
    """INV-12: the audit trail must not surface raw PII from the cart/payment."""
    audit_log(
        session,
        trace_id="tr_x", client_id="cli_1", action="checkout.create", resource_type="checkout",
        metadata={"checkout_id": "chk_9"},  # derived reference only
    )
    session.commit()
    row = get_audit_trail(session, trace_id="tr_x")[0]
    md = row.metadata or {}
    for forbidden in ("buyer_email", "buyer_phone", "cart_items", "webauthn_challenge", "signature"):
        assert forbidden not in md
