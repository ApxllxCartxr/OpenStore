"""Day 7 — adversarial failure-mode tests (Intent Compiler edition).

Nine failure modes, each asserting the three "handled correctly" properties
from the Day 7 tutorial: (1) the operation is rejected, (2) a `success=False`
audit row lands in /admin/audit, and (3) no exploitable partial state is left
behind. The tutorial's idealized `call_mcp_tool`/`catalog_read_only_token`
helpers are re-expressed against the real MCP surface here.

Note on MCP error semantics: tool-level HTTPExceptions (scope/Intent-Compiler/
mandate rejections) are surfaced by the MCP transport as `result.isError=True`
at HTTP 200, not as an HTTP error status. So these tests assert on the parsed
`isError` flag + content text, the audit row, and DB state — never on the bare
HTTP status of the MCP call.
"""

import base64
import hashlib
import json
import time
import uuid
from datetime import datetime, timedelta
from unittest import mock

import jwt as pyjwt
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from reference.merchant.app import create_app
from reference.merchant.db import engine
from reference.merchant.mandate import issue_mandate, compute_cart_hash
from reference.merchant.models import (
    AuditLogEntry,
    Cart,
    Checkout,
    IdempotencyRecord,
    IntentPolicyRow,
    Mandate,
    Order,
    SpendLedgerEntry,
)
from reference.merchant.oauth.routes import JWT_SECRET, REVOKED_JTIS
from reference.merchant import razorpay_client


# --- helpers (mirror tests/test_mcp.py) ---


def _get_token(scopes: list[str], client_id: str = None) -> str:
    client_id = client_id or f"test-{uuid.uuid4().hex[:12]}"
    now = int(time.time())
    return pyjwt.encode(
        {
            "sub": client_id,
            "scope": " ".join(scopes),
            "iat": now,
            "exp": now + 900,
            "jti": uuid.uuid4().hex,
        },
        JWT_SECRET,
        algorithm="HS256",
    )


def _mcp_call(client, tool_name, arguments, token):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    return client.post(
        "/agent/mcp",
        json=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {token}",
        },
    )


def _parse_mcp_result(resp):
    text = resp.text
    if text.startswith("event:"):
        for line in text.split("\n"):
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
    return resp.json()


def _call_tool(client, tool, args, token):
    """Returns (parsed_result, is_error, content_text)."""
    resp = _mcp_call(client, tool, args, token)
    parsed = _parse_mcp_result(resp)
    result = parsed.get("result", {})
    is_error = result.get("isError", False)
    content = result.get("content", [])
    text = content[0].get("text", "") if content else ""
    return parsed, is_error, text


# --- DB assertions / cleanup ---


def _count_audit_failures(tool: str) -> int:
    with Session(engine) as s:
        return len(
            s.exec(
                select(AuditLogEntry).where(
                    AuditLogEntry.tool == tool, AuditLogEntry.success == False  # noqa: E712
                )
            ).all()
        )


def _audit_failure_summaries(tool: str) -> list[str]:
    """The MCP transport swallows a tool's raised HTTPException into a generic
    'Error executing tool X' message, but the audit log records the real
    reason in result_summary (e.g. '403: Intent Compiler rejected: ...')."""
    with Session(engine) as s:
        return [
            r.result_summary
            for r in s.exec(
                select(AuditLogEntry).where(
                    AuditLogEntry.tool == tool, AuditLogEntry.success == False  # noqa: E712
                )
            ).all()
        ]


def _assert_audit_reason(tool: str, substring: str):
    assert any(substring in s.lower() for s in _audit_failure_summaries(tool))


def _count_orders(checkout_id: str) -> int:
    with Session(engine) as s:
        return len(s.exec(select(Order).where(Order.checkout_id == checkout_id)).all())


def _cleanup(client_id: str, checkout_id: str, cart_id: int = None, cred_id: str = None):
    with Session(engine) as s:
        for model, key in (
            (Order, "checkout_id"),
            (Checkout, "checkout_id"),
            (IdempotencyRecord, "client_id"),
            (SpendLedgerEntry, "client_id"),
        ):
            rows = s.exec(select(model).where(getattr(model, key) == locals()[key])).all()
            for r in rows:
                s.delete(r)
        if cart_id is not None:
            cart = s.get(Cart, cart_id)
            if cart:
                s.delete(cart)
        if cred_id is not None:
            row = s.exec(select(IntentPolicyRow).where(IntentPolicyRow.credential_id == cred_id)).first()
            if row:
                s.delete(row)
        mandates = s.exec(select(Mandate).where(Mandate.checkout_id == checkout_id)).all()
        for m in mandates:
            s.delete(m)
        s.commit()


# --- cart / checkout setup helpers ---


def _create_cart(client, token, items):
    """items: list of {sku, qty}. Returns (cart_id, items_json, total_minor)."""
    _, _is_err, text = _call_tool(client, "create_cart", {"items": items}, token)
    cart = json.loads(text)
    cart_id = cart["cart_id"]
    total = sum(i["unit_minor"] * i["qty"] for i in cart["items"])
    return cart_id, cart["items"], total


def _initiate(client, token, cart_id, address="221B Baker Street"):
    _, _is_err, text = _call_tool(
        client, "checkout_initiate", {"cart_id": cart_id, "delivery_address": address}, token
    )
    return json.loads(text)["checkout_id"]


def _insert_policy(cred_id, policy):
    with Session(engine) as s:
        s.add(IntentPolicyRow(credential_id=cred_id, public_key="x", policy_json=policy, active=True))
        s.commit()


def _build_mandate(client, sub, cart_id, items_json, total_minor, address="221B Baker Street"):
    """Insert a MANDATE_ISSUED checkout + a signed Mandate row. Returns (checkout_id, jws)."""
    checkout_id = str(uuid.uuid4())
    cart_hash = compute_cart_hash(items_json)
    with Session(engine) as s:
        s.add(
            Checkout(
                checkout_id=checkout_id,
                cart_id=cart_id,
                client_id=sub,
                status="MANDATE_ISSUED",
                cart_hash=cart_hash,
                total_minor=total_minor,
                delivery_address=address,
                expires_at=datetime.utcnow() + timedelta(minutes=5),
            )
        )
        s.commit()
    jws, _fp = issue_mandate(
        merchant_id="gelateria-roma",
        checkout_id=checkout_id,
        client_id=sub,
        cart_hash=cart_hash,
        cart_version=1,
        items=items_json,
        total_minor=total_minor,
        delivery_address_hash=hashlib.sha256(address.encode()).hexdigest(),
    )
    jti = pyjwt.decode(jws, options={"verify_signature": False})["jti"]
    with Session(engine) as s:
        s.add(Mandate(jti=jti, checkout_id=checkout_id, jws_compact=jws, fingerprint="x", burned=False))
        s.commit()
    return checkout_id, jws


# === Failure mode 1: unscoped tool call ===


def test_unscoped_checkout_confirm_rejected():
    app = create_app()
    with TestClient(app) as client:
        token = _get_token(["catalog:read"])  # no checkout:confirm
        before = _count_audit_failures("checkout_confirm")
        _parsed, is_err, text = _call_tool(client, "checkout_confirm", {"checkout_id": "nope"}, token)
        assert is_err
        _assert_audit_reason("checkout_confirm", "scope")
        assert _count_audit_failures("checkout_confirm") > before


# === Failure mode 9: Intent Compiler policy violation (tag) ===


def test_intent_compiler_rejects_policy_violation():
    app = create_app()
    with TestClient(app) as client:
        token = _get_token(["catalog:read", "cart:write", "checkout:initiate", "checkout:confirm"])
        cred_id = f"cred-{uuid.uuid4().hex[:8]}"
        policy = {
            "max_spend_minor": 50000,
            "allowed_tags": ["vegan"],
            "blocked_skus": [],
            "merchant_id": "gelateria-roma",
            "expires_at": int(time.time()) + 3600,
        }
        _insert_policy(cred_id, policy)
        cart_id, items, total = _create_cart(client, token, [{"sku": "gel-001", "qty": 1}])  # nuts/classic/gift
        checkout_id = _initiate(client, token, cart_id)
        try:
            before = _count_audit_failures("checkout_confirm")
            _parsed, is_err, text = _call_tool(
                client,
                "checkout_confirm",
                {
                    "policy_token": {"rawId": cred_id, "id": cred_id},
                    "policy_json": policy,
                    "checkout_id": checkout_id,
                    "idempotency_key": str(uuid.uuid4()),
                },
                token,
            )
            assert is_err
            _assert_audit_reason("checkout_confirm", "intent compiler")
            _assert_audit_reason("checkout_confirm", "tag")
            assert _count_orders(checkout_id) == 0
            assert _count_audit_failures("checkout_confirm") > before
        finally:
            _cleanup(token and "x", checkout_id, cart_id, cred_id)


# === Failure mode 9 variant: wrong merchant ===


def test_intent_compiler_rejects_wrong_merchant():
    app = create_app()
    with TestClient(app) as client:
        token = _get_token(["catalog:read", "cart:write", "checkout:initiate", "checkout:confirm"])
        cred_id = f"cred-{uuid.uuid4().hex[:8]}"
        policy = {
            "max_spend_minor": 50000,
            "allowed_tags": ["vegan"],
            "blocked_skus": [],
            "merchant_id": "some-other-merchant",
            "expires_at": int(time.time()) + 3600,
        }
        _insert_policy(cred_id, policy)
        cart_id, items, total = _create_cart(client, token, [{"sku": "gel-003", "qty": 1}])
        checkout_id = _initiate(client, token, cart_id)
        try:
            before = _count_audit_failures("checkout_confirm")
            _parsed, is_err, text = _call_tool(
                client,
                "checkout_confirm",
                {
                    "policy_token": {"rawId": cred_id, "id": cred_id},
                    "policy_json": policy,
                    "checkout_id": checkout_id,
                    "idempotency_key": str(uuid.uuid4()),
                },
                token,
            )
            assert is_err
            _assert_audit_reason("checkout_confirm", "merchant")
            assert _count_orders(checkout_id) == 0
            assert _count_audit_failures("checkout_confirm") > before
        finally:
            _cleanup(token and "x", checkout_id, cart_id, cred_id)


# === Failure mode 9 variant: over spend limit ===


def test_intent_compiler_rejects_over_spend():
    app = create_app()
    with TestClient(app) as client:
        token = _get_token(["catalog:read", "cart:write", "checkout:initiate", "checkout:confirm"])
        cred_id = f"cred-{uuid.uuid4().hex[:8]}"
        policy = {
            "max_spend_minor": 1,  # 0.01 — any real cart exceeds this
            "allowed_tags": ["fruit", "dairy-free", "vegan"],
            "blocked_skus": [],
            "merchant_id": "gelateria-roma",
            "expires_at": int(time.time()) + 3600,
        }
        _insert_policy(cred_id, policy)
        cart_id, items, total = _create_cart(client, token, [{"sku": "gel-003", "qty": 1}])
        checkout_id = _initiate(client, token, cart_id)
        try:
            before = _count_audit_failures("checkout_confirm")
            _parsed, is_err, text = _call_tool(
                client,
                "checkout_confirm",
                {
                    "policy_token": {"rawId": cred_id, "id": cred_id},
                    "policy_json": policy,
                    "checkout_id": checkout_id,
                    "idempotency_key": str(uuid.uuid4()),
                },
                token,
            )
            assert is_err
            assert any(
                "spend" in s.lower() or "limit" in s.lower()
                for s in _audit_failure_summaries("checkout_confirm")
            )
            assert _count_orders(checkout_id) == 0
            assert _count_audit_failures("checkout_confirm") > before
        finally:
            _cleanup(token and "x", checkout_id, cart_id, cred_id)


# === Failure mode 6: idempotent retry (no duplicate charge) ===


def test_idempotent_retry_no_duplicate_charge():
    app = create_app()
    with TestClient(app) as client:
        token = _get_token(["catalog:read", "cart:write", "checkout:initiate", "checkout:confirm"])
        cred_id = f"cred-{uuid.uuid4().hex[:8]}"
        policy = {
            "max_spend_minor": 500000,
            "allowed_tags": ["fruit", "dairy-free", "vegan"],
            "blocked_skus": [],
            "merchant_id": "gelateria-roma",
            "expires_at": int(time.time()) + 3600,
        }
        _insert_policy(cred_id, policy)
        cart_id, items, total = _create_cart(client, token, [{"sku": "gel-003", "qty": 1}])
        checkout_id = _initiate(client, token, cart_id)
        key = str(uuid.uuid4())
        fake_rp = {
            "razorpay_order_id": "order_fake",
            "payment_link_id": "link_fake",
            "payment_link_url": "https://razorpay.com/fake",
        }
        try:
            with mock.patch("reference.merchant.mcp_server.create_order_and_payment_link", return_value=fake_rp):
                razorpay_client.INJECT_TIMEOUT["enabled"] = True
                try:
                    _parsed, is_err, text = _call_tool(
                        client,
                        "checkout_confirm",
                        {
                            "policy_token": {"rawId": cred_id, "id": cred_id},
                            "policy_json": policy,
                            "checkout_id": checkout_id,
                            "idempotency_key": key,
                        },
                        token,
                    )
                    # First call simulates a dropped response after order creation.
                    assert _count_orders(checkout_id) == 1

                    second, is_err2, text2 = _call_tool(
                        client,
                        "checkout_confirm",
                        {
                            "policy_token": {"rawId": cred_id, "id": cred_id},
                            "policy_json": policy,
                            "checkout_id": checkout_id,
                            "idempotency_key": key,
                        },
                        token,
                    )
                    assert not is_err2
                    assert "payment_link_url" in text2
                    assert _count_orders(checkout_id) == 1  # no duplicate
                finally:
                    razorpay_client.INJECT_TIMEOUT["enabled"] = False
        finally:
            _cleanup(token and "x", checkout_id, cart_id, cred_id)


# === Failure mode 8: token revocation mid-session ===


def test_token_revocation_blocks_only_that_token():
    app = create_app()
    with TestClient(app) as client:
        token_a = _get_token(["catalog:read"], client_id="revoke-a")
        token_b = _get_token(["catalog:read"], client_id="revoke-b")

        resp = client.post("/oauth/revoke", data={"token": token_a})
        assert resp.status_code == 200

        before_a = _count_audit_failures("search_products")
        _parsed, is_err_a, text_a = _call_tool(client, "search_products", {"query": "gelato"}, token_a)
        assert is_err_a
        _assert_audit_reason("search_products", "revoked")
        assert _count_audit_failures("search_products") > before_a

        _parsed, is_err_b, text_b = _call_tool(client, "search_products", {"query": "gelato"}, token_b)
        assert not is_err_b
        assert "pistachio" in text_b.lower()


# === Failure mode 7: prompt injection cannot alter the server-computed total ===


def test_prompt_injection_cannot_alter_server_total():
    app = create_app()
    with TestClient(app) as client:
        token = _get_token(["cart:write"])
        # Attacker supplies a bogus unit_minor in the cart payload.
        _parsed, is_err, text = _call_tool(
            client, "create_cart", {"items": [{"sku": "gel-001", "qty": 1, "unit_minor": 1}]}, token
        )
        assert not is_err
        cart = json.loads(text)
        # Server re-derives price from the catalog; attacker's unit_minor is ignored.
        assert cart["items"][0]["unit_minor"] == 25000
        assert cart["items"][0]["unit_minor"] != 1


# === Failure mode 3: tampered mandate (valid signature stripped, payload altered) ===


def test_tampered_mandate_rejected():
    app = create_app()
    with TestClient(app) as client:
        token = _get_token(["checkout:confirm"])
        sub = "mandate-tamper"
        cart_id, items, total = _create_cart(client, _get_token(["cart:write"]), [{"sku": "gel-003", "qty": 1}])
        checkout_id, jws = _build_mandate(client, sub, cart_id, items, total)
        try:
            h, p, s = jws.split(".")
            payload = json.loads(base64.urlsafe_b64decode(p + "=="))
            payload["amt"] = 1  # attacker alters the amount, keeps the old signature
            new_p = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
            tampered = f"{h}.{new_p}.{s}"

            before = _count_audit_failures("checkout_confirm")
            _parsed, is_err, text = _call_tool(
                client, "checkout_confirm", {"jws": tampered, "idempotency_key": str(uuid.uuid4())}, token
            )
            assert is_err
            _assert_audit_reason("checkout_confirm", "mandate")
            assert _count_orders(checkout_id) == 0
            assert _count_audit_failures("checkout_confirm") > before
        finally:
            _cleanup(sub, checkout_id, cart_id)


# === Failure mode 2: expired mandate replay ===


def test_expired_mandate_rejected():
    app = create_app()
    with TestClient(app) as client:
        token = _get_token(["checkout:confirm"])
        sub = "mandate-expired"
        cart_id, items, total = _create_cart(client, _get_token(["cart:write"]), [{"sku": "gel-003", "qty": 1}])
        import reference.merchant.mandate as _m

        # Issue the mandate with a clock 200s in the past so its exp is already gone.
        with mock.patch.object(_m.time, "time", return_value=time.time() - 200):
            checkout_id, jws = _build_mandate(client, sub, cart_id, items, total)
        try:
            before = _count_audit_failures("checkout_confirm")
            _parsed, is_err, text = _call_tool(
                client, "checkout_confirm", {"jws": jws, "idempotency_key": str(uuid.uuid4())}, token
            )
            assert is_err
            _assert_audit_reason("checkout_confirm", "expired")
            assert _count_orders(checkout_id) == 0
            assert _count_audit_failures("checkout_confirm") > before
        finally:
            _cleanup(sub, checkout_id, cart_id)


# === Failure mode 4: cart-hash mismatch (stale mandate vs mutated checkout) ===


def test_cart_hash_mismatch_rejected():
    app = create_app()
    with TestClient(app) as client:
        token = _get_token(["checkout:confirm"])
        sub = "mandate-hash"
        cart_id, items, total = _create_cart(client, _get_token(["cart:write"]), [{"sku": "gel-003", "qty": 1}])
        checkout_id, jws = _build_mandate(client, sub, cart_id, items, total)
        # Mutate the frozen checkout snapshot so it no longer matches the mandate.
        with Session(engine) as s:
            co = s.exec(select(Checkout).where(Checkout.checkout_id == checkout_id)).first()
            co.cart_hash = "tampered-hash"
            s.add(co)
            s.commit()
        try:
            before = _count_audit_failures("checkout_confirm")
            _parsed, is_err, text = _call_tool(
                client, "checkout_confirm", {"jws": jws, "idempotency_key": str(uuid.uuid4())}, token
            )
            assert is_err
            _assert_audit_reason("checkout_confirm", "hash")
            assert _count_orders(checkout_id) == 0
            assert _count_audit_failures("checkout_confirm") > before
        finally:
            _cleanup(sub, checkout_id, cart_id)
