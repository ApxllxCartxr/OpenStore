import hashlib
import time
import uuid

from mcp.server.mcpserver import MCPServer, Context
from fastapi import HTTPException
from sqlmodel import Session, select

from reference.merchant.catalog.yaml_adapter import YAMLCatalogAdapter
from reference.merchant.config import settings
from reference.merchant.db import engine
from reference.merchant.mandate import canonical_json_bytes, verify_mandate
from reference.merchant.models import (
    Cart, Checkout, Mandate, Order, IdempotencyRecord, SpendLedgerEntry,
    IntentPolicyRow, PolicyChallenge,
)
from reference.merchant.mcp_auth import get_claims_from_context
from reference.merchant.policy import check_rate_limit, rolling_spend_minor
from reference.merchant.audit import audited_tool
from reference.merchant.trace import emit
from reference.merchant.checkout import checkout_initiate as _checkout_initiate
from reference.merchant.intent_compiler import verify_cart_against_policy
from reference.merchant.razorpay_client import create_order_and_payment_link, INJECT_TIMEOUT
from openstore.mcp_server import build_mcp_server
from reference.merchant.openstore_bridge import build_openstore_runtime

# The reference merchant CONSUMES the generic openstore core: the same MCP server
# exposes both the generic agent-commerce tools (discover/mandate/quote/order/...)
# and the merchant-specific catalog/cart tools, all backed by one trust system.
mcp = build_mcp_server(build_openstore_runtime())
_catalog = YAMLCatalogAdapter(settings.merchant_config_path)


@mcp.tool()
@audited_tool("search_products")
def search_products(query: str, ctx: Context) -> list[dict]:
    """Search the merchant's product catalog by keyword. Returns matching products
    with sku, name, description, and price in minor units (paise)."""
    claims = get_claims_from_context(ctx, "catalog:read")
    if not check_rate_limit(claims["sub"], "search_products"):
        raise HTTPException(429, "Rate limit exceeded")
    results = _catalog.search_products(query)
    return [p.model_dump() for p in results]


@mcp.tool()
@audited_tool("get_product")
def get_product(sku: str, ctx: Context) -> dict:
    """Fetch full details for one product by its SKU."""
    claims = get_claims_from_context(ctx, "catalog:read")
    if not check_rate_limit(claims["sub"], "get_product"):
        raise HTTPException(429, "Rate limit exceeded")
    product = _catalog.get_product(sku)
    if product is None:
        raise HTTPException(404, f"No product with sku {sku}")
    return product.model_dump()


@mcp.tool()
@audited_tool("create_cart")
def create_cart(items: list[dict], ctx: Context) -> dict:
    """Create a new cart. items is a list of {sku, qty} — price is never taken
    from the caller, it is always looked up server-side from the current catalog."""
    claims = get_claims_from_context(ctx, "cart:write")
    if not check_rate_limit(claims["sub"], "create_cart"):
        raise HTTPException(429, "Rate limit exceeded")

    validated_items = _validate_and_price(items)
    with Session(engine) as session:
        cart = Cart(client_id=claims["sub"], version=1, items_json=validated_items)
        session.add(cart)
        session.commit()
        session.refresh(cart)
        return {"cart_id": cart.id, "version": cart.version, "items": cart.items_json}


@mcp.tool()
@audited_tool("update_cart")
def update_cart(cart_id: int, items: list[dict], ctx: Context) -> dict:
    """Replace a cart's items. Bumps cart_version on every mutation — an old
    cart_version referenced anywhere downstream (e.g. a stale mandate) is invalid."""
    claims = get_claims_from_context(ctx, "cart:write")
    if not check_rate_limit(claims["sub"], "update_cart"):
        raise HTTPException(429, "Rate limit exceeded")

    validated_items = _validate_and_price(items)
    with Session(engine) as session:
        cart = session.get(Cart, cart_id)
        if cart is None or cart.client_id != claims["sub"]:
            raise HTTPException(404, "Cart not found")
        cart.items_json = validated_items
        cart.version += 1
        session.add(cart)
        session.commit()
        session.refresh(cart)
        return {"cart_id": cart.id, "version": cart.version, "items": cart.items_json}


def _validate_and_price(items: list[dict]) -> list[dict]:
    """Re-derives price for every item from the live catalog. This is the
    concrete implementation of 'never trust an agent-supplied total' from
    the cart layer: only sku and qty come from the caller."""
    priced = []
    for item in items:
        product = _catalog.get_product(item["sku"])
        if product is None:
            raise HTTPException(400, f"Unknown sku: {item['sku']}")
        priced.append({
            "sku": product.sku,
            "qty": item["qty"],
            "unit_minor": product.price_minor,
        })
    return priced


@mcp.tool()
@audited_tool("checkout_initiate")
def checkout_initiate(cart_id: int, delivery_address: str, ctx: Context, trace_id: str = None) -> dict:
    """Initiates checkout for a cart. Recomputes the total from the DB,
    checks spend caps, freezes an immutable snapshot. In the Intent Compiler
    flow, no OTP is issued — the agent proceeds directly to checkout_confirm,
    where the Intent Compiler verifies the cart against the signed policy."""
    claims = get_claims_from_context(ctx, "checkout:initiate")
    if not check_rate_limit(claims["sub"], "checkout_initiate"):
        raise HTTPException(429, "Rate limit exceeded")
    return _checkout_initiate(cart_id, delivery_address, claims["sub"], trace_id)


PER_TX_CAP_MINOR = 50000
ROLLING_CAP_MINOR = 200000


@mcp.tool()
@audited_tool("checkout_confirm")
def checkout_confirm(
    checkout_id: str = None,        # required: which checkout to confirm
    policy_token: dict = None,      # assertion dict from WebAuthn signing ceremony
    policy_json: dict = None,       # the IntentPolicy the agent claims was signed
    jws: str = None,                # optional: for OTP/mandate fallback path
    idempotency_key: str = None,
    ctx: Context = None,
    trace_id: str = None,
) -> dict:
    """Confirms a checkout. In the Intent Compiler path, the agent presents
    a policy_token (WebAuthn assertion) and the policy_json it was signed
    against. The server verifies the assertion signature and confirms the
    nonce maps to the policy hash before running the Intent Compiler.
    In the fallback OTP path, a signed mandate is used instead."""
    claims = get_claims_from_context(ctx, "checkout:confirm")
    if not check_rate_limit(claims["sub"], "checkout_confirm"):
        raise HTTPException(429, "Rate limit exceeded")

    with Session(engine) as session:
        # Idempotency check
        if idempotency_key is not None:
            existing = session.exec(
                select(IdempotencyRecord)
                .where(IdempotencyRecord.client_id == claims["sub"])
                .where(IdempotencyRecord.idempotency_key == idempotency_key)
            ).first()
            if existing is not None:
                emit("merchant-server", "Idempotent replay", {}, trace_id, "info")
                return existing.response_json

        # === PATH A: Intent Compiler (primary) ===
        if policy_token is not None and policy_json is not None:
            if checkout_id is None:
                raise HTTPException(400, "checkout_id required for Intent Compiler path")

            checkout = session.exec(
                select(Checkout).where(Checkout.checkout_id == checkout_id)
            ).first()
            if checkout is None or checkout.client_id != claims["sub"]:
                raise HTTPException(404, "Checkout not found")
            if checkout.status != "POLICY_VERIFIED":
                raise HTTPException(400, "Checkout not in POLICY_VERIFIED state")

            # Look up the credential to get the stored policy
            credential_id = policy_token.get("rawId") or policy_token.get("id", "")
            policy_row = session.exec(
                select(IntentPolicyRow)
                .where(IntentPolicyRow.credential_id == credential_id)
                .where(IntentPolicyRow.active == True)
            ).first()
            if policy_row is None:
                raise HTTPException(403, "Unknown credential — sign a policy first")

            # Verify the policy_json matches what was signed (hash comparison)
            stored_policy_json = policy_row.policy_json
            if hashlib.sha256(canonical_json_bytes(stored_policy_json)).hexdigest() != \
               hashlib.sha256(canonical_json_bytes(policy_json)).hexdigest():
                raise HTTPException(403, "Policy mismatch — assertion was signed for a different policy")

            # Run the Intent Compiler: cart vs signed policy
            cart = session.get(Cart, checkout.cart_id)
            if cart is None:
                raise HTTPException(400, "Cart not found")

            ok, reason = verify_cart_against_policy(
                cart_items=cart.items_json,
                policy=policy_json,
                merchant_id=settings.merchant_id,
                product_lookup=_catalog.get_product,
            )
            if not ok:
                emit("merchant-server", "Intent Compiler REJECTED checkout",
                     {"reason": reason, "checkout_id": checkout_id},
                     trace_id, "blocked")
                checkout.status = "REJECTED"
                session.add(checkout)
                session.commit()
                raise HTTPException(403, f"Intent Compiler rejected: {reason}")

            already_spent = rolling_spend_minor(session, claims["sub"])
            if already_spent + checkout.total_minor > ROLLING_CAP_MINOR:
                raise HTTPException(400, "Rolling spend cap exceeded at confirm time")

            if idempotency_key is None:
                raise HTTPException(400, "idempotency_key required")

            evidence_inputs = {
                "policy_row": policy_row,
                "policy_token": policy_token,
                "claims": claims,
            }

            if INJECT_TIMEOUT["enabled"]:
                rp_result = create_order_and_payment_link(checkout.total_minor, checkout.checkout_id)
                _persist_confirm_result(session, checkout, claims["sub"], idempotency_key, rp_result, trace_id, evidence_inputs=evidence_inputs)
                INJECT_TIMEOUT["enabled"] = False
                raise HTTPException(504, "Simulated timeout — response dropped after order creation")

            rp_result = create_order_and_payment_link(checkout.total_minor, checkout.checkout_id)
            result = _persist_confirm_result(session, checkout, claims["sub"], idempotency_key, rp_result, trace_id, evidence_inputs=evidence_inputs)
            return result

        # === PATH B: OTP + Mandate (fallback, same as original plan) ===
        if jws is not None:
            try:
                mandate_claims = verify_mandate(jws)
            except Exception as e:
                emit("merchant-server", "Mandate verification failed", {"error": str(e)}, trace_id, "blocked")
                raise HTTPException(400, f"Invalid mandate: {e}")

            if mandate_claims["exp"] < time.time():
                emit("merchant-server", "Mandate expired", {}, trace_id, "blocked")
                raise HTTPException(400, "Mandate expired")

            checkout = session.exec(
                select(Checkout).where(Checkout.checkout_id == mandate_claims["chk"])
            ).first()

            if checkout is None or checkout.status != "MANDATE_ISSUED":
                emit("merchant-server", "Checkout not in MANDATE_ISSUED state", {}, trace_id, "blocked")
                raise HTTPException(400, "Checkout not awaiting confirmation")

            if mandate_claims["cart"]["hash"] != checkout.cart_hash:
                emit("merchant-server", "Cart hash mismatch — possible tampering", {}, trace_id, "blocked")
                raise HTTPException(400, "Cart hash mismatch")

            if mandate_claims["amt"] != checkout.total_minor:
                emit("merchant-server", "Amount mismatch", {}, trace_id, "blocked")
                raise HTTPException(400, "Amount mismatch")

            mandate_row = session.exec(select(Mandate).where(Mandate.jti == mandate_claims["jti"])).first()
            if mandate_row is None or mandate_row.burned:
                emit("merchant-server", "Mandate replay detected", {"jti": mandate_claims["jti"]}, trace_id, "blocked")
                raise HTTPException(400, "Mandate already used or unknown")

            already_spent = rolling_spend_minor(session, claims["sub"])
            if already_spent + checkout.total_minor > ROLLING_CAP_MINOR:
                emit("merchant-server", "Spend cap exceeded at confirm time", {}, trace_id, "blocked")
                raise HTTPException(400, "Rolling spend cap exceeded")

            mandate_row.burned = True
            session.add(mandate_row)
            session.commit()

            if INJECT_TIMEOUT["enabled"]:
                rp_result = create_order_and_payment_link(checkout.total_minor, checkout.checkout_id)
                _persist_confirm_result(session, checkout, claims["sub"], idempotency_key, rp_result, trace_id)
                INJECT_TIMEOUT["enabled"] = False
                raise HTTPException(504, "Simulated timeout — response dropped after order creation")

            rp_result = create_order_and_payment_link(checkout.total_minor, checkout.checkout_id)
            result = _persist_confirm_result(session, checkout, claims["sub"], idempotency_key, rp_result, trace_id)
            return result

        raise HTTPException(400, "Provide policy_token + policy_json (Intent Compiler) or jws (mandate fallback)")


def _persist_confirm_result(session, checkout, client_id, idempotency_key, rp_result, trace_id, evidence_inputs=None) -> dict:
    checkout.status = "ORDER_CREATED"
    session.add(checkout)
    session.add(Order(
        checkout_id=checkout.checkout_id,
        razorpay_order_id=rp_result["razorpay_order_id"],
        razorpay_payment_link_id=rp_result["payment_link_id"],
        status="CREATED",
        total_minor=checkout.total_minor,
    ))
    session.add(SpendLedgerEntry(client_id=client_id, amount_minor=checkout.total_minor))

    # R9.3.x: issue a PoAI evidence bundle inside the same transaction that
    # settles the charge, so a settled payment always has its receipt. Best
    # effort: a verification gap must never break an already-settled checkout.
    if evidence_inputs:
        try:
            from reference.merchant.evidence_issuer import issue_evidence
            issue_evidence(session, checkout=checkout, **evidence_inputs)
        except Exception as exc:  # noqa: BLE001
            import logging
            logging.getLogger("poai.issuer").warning("evidence issuance skipped: %s", exc)

    result = {
        "checkout_id": checkout.checkout_id,
        "payment_link_url": rp_result["payment_link_url"],
        "status": "ORDER_CREATED",
    }
    session.add(IdempotencyRecord(
        client_id=client_id,
        idempotency_key=idempotency_key,
        response_json=result,
    ))
    session.commit()
    emit("merchant-server", "Order created", {"checkout_id": checkout.checkout_id}, trace_id, "executed")
    return result
