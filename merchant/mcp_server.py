import time
import uuid

from mcp.server.mcpserver import MCPServer, Context
from fastapi import HTTPException
from sqlmodel import Session, select

from merchant.catalog.yaml_adapter import YAMLCatalogAdapter
from merchant.config import settings
from merchant.db import engine
from merchant.models import (
    Cart, Checkout, Mandate, Order, IdempotencyRecord, SpendLedgerEntry,
)
from merchant.mcp_auth import get_claims_from_context
from merchant.policy import check_rate_limit, rolling_spend_minor
from merchant.audit import audited_tool
from merchant.trace import emit
from merchant.checkout import checkout_initiate as _checkout_initiate
from merchant.mandate import verify_mandate
from merchant.razorpay_client import create_order_and_payment_link, INJECT_TIMEOUT

mcp = MCPServer("openstore-catalog")
catalog = YAMLCatalogAdapter(settings.merchant_config_path)


@mcp.tool()
@audited_tool("search_products")
def search_products(query: str, ctx: Context) -> list[dict]:
    """Search the merchant's product catalog by keyword. Returns matching products
    with sku, name, description, and price in minor units (paise)."""
    claims = get_claims_from_context(ctx, "catalog:read")
    if not check_rate_limit(claims["sub"], "search_products"):
        raise HTTPException(429, "Rate limit exceeded")
    results = catalog.search_products(query)
    return [p.model_dump() for p in results]


@mcp.tool()
@audited_tool("get_product")
def get_product(sku: str, ctx: Context) -> dict:
    """Fetch full details for one product by its SKU."""
    claims = get_claims_from_context(ctx, "catalog:read")
    if not check_rate_limit(claims["sub"], "get_product"):
        raise HTTPException(429, "Rate limit exceeded")
    product = catalog.get_product(sku)
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
        product = catalog.get_product(item["sku"])
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
    """Initiates checkout for a cart. Recomputes the total from the DB, checks
    spend caps, freezes an immutable snapshot, and sends an OTP to the human
    approver via DM. Never touches Razorpay."""
    claims = get_claims_from_context(ctx, "checkout:initiate")
    if not check_rate_limit(claims["sub"], "checkout_initiate"):
        raise HTTPException(429, "Rate limit exceeded")
    return _checkout_initiate(cart_id, delivery_address, claims["sub"], trace_id)


PER_TX_CAP_MINOR = 50000
ROLLING_CAP_MINOR = 200000


@mcp.tool()
@audited_tool("checkout_confirm")
def checkout_confirm(jws: str, idempotency_key: str, ctx: Context, trace_id: str = None) -> dict:
    """Confirms a checkout given a signed mandate. Runs the full verification
    ladder — signature, claims, checkout state, cart hash, amount, jti unburned,
    spend cap re-check, idempotency — before ever calling Razorpay."""
    claims = get_claims_from_context(ctx, "checkout:confirm")
    if not check_rate_limit(claims["sub"], "checkout_confirm"):
        raise HTTPException(429, "Rate limit exceeded")

    with Session(engine) as session:
        existing = session.exec(
            select(IdempotencyRecord)
            .where(IdempotencyRecord.client_id == claims["sub"])
            .where(IdempotencyRecord.idempotency_key == idempotency_key)
        ).first()
        if existing is not None:
            emit("merchant-server", "Idempotent replay — returning cached result", {}, trace_id, "info")
            return existing.response_json

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


def _persist_confirm_result(session, checkout, client_id, idempotency_key, rp_result, trace_id) -> dict:
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
