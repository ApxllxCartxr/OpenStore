from mcp.server.mcpserver import MCPServer, Context
from fastapi import HTTPException
from sqlmodel import Session

from merchant.catalog.yaml_adapter import YAMLCatalogAdapter
from merchant.config import settings
from merchant.db import engine
from merchant.models import Cart
from merchant.mcp_auth import get_claims_from_context
from merchant.policy import check_rate_limit
from merchant.audit import audited_tool

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
