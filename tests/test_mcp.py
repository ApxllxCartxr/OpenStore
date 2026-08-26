import json
import secrets
import time

from fastapi.testclient import TestClient
import jwt as pyjwt

from merchant.app import create_app
from merchant.oauth.routes import JWT_SECRET


def _get_token(scopes: list[str], client_id: str = "test-mcp-client") -> str:
    now = int(time.time())
    return pyjwt.encode(
        {
            "sub": client_id,
            "scope": " ".join(scopes),
            "iat": now,
            "exp": now + 900,
            "jti": secrets.token_urlsafe(16),
        },
        JWT_SECRET,
        algorithm="HS256",
    )


def _mcp_call(client: TestClient, tool_name: str, arguments: dict, token: str):
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


def _parse_mcp_result(resp) -> dict:
    text = resp.text
    if text.startswith("event:"):
        for line in text.split("\n"):
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
    return resp.json()


def _extract_content(parsed: dict):
    content = parsed.get("result", {}).get("content", [])
    if content:
        first_text = content[0].get("text", "{}")
        first = json.loads(first_text)
        if isinstance(first, dict) and len(content) > 1:
            return [first] + [json.loads(c["text"]) for c in content[1:]]
        return first
    return parsed


# --- Tool tests ---


def test_search_products_returns_results():
    app = create_app()
    with TestClient(app) as client:
        token = _get_token(["catalog:read"])
        resp = _mcp_call(client, "search_products", {"query": "pistachio"}, token)
        assert resp.status_code == 200
        results = _extract_content(_parse_mcp_result(resp))
        assert isinstance(results, list)
        assert any("pistachio" in p["name"].lower() for p in results)


def test_get_product_returns_product():
    app = create_app()
    with TestClient(app) as client:
        token = _get_token(["catalog:read"])
        resp = _mcp_call(client, "get_product", {"sku": "gel-001"}, token)
        assert resp.status_code == 200
        product = _extract_content(_parse_mcp_result(resp))
        assert product["sku"] == "gel-001"
        assert product["price_minor"] == 25000


def test_create_cart_stores_items():
    app = create_app()
    with TestClient(app) as client:
        token = _get_token(["cart:write"])
        resp = _mcp_call(client, "create_cart", {"items": [{"sku": "gel-001", "qty": 2}]}, token)
        assert resp.status_code == 200
        cart = _extract_content(_parse_mcp_result(resp))
        assert cart["cart_id"] is not None
        assert cart["version"] == 1
        assert len(cart["items"]) == 1
        assert cart["items"][0]["sku"] == "gel-001"
        assert cart["items"][0]["unit_minor"] == 25000


def test_update_cart_bumps_version():
    app = create_app()
    with TestClient(app) as client:
        token = _get_token(["cart:write"])
        create_resp = _mcp_call(client, "create_cart", {"items": [{"sku": "gel-001", "qty": 1}]}, token)
        cart = _extract_content(_parse_mcp_result(create_resp))
        cart_id = cart["cart_id"]

        resp = _mcp_call(client, "update_cart", {"cart_id": cart_id, "items": [{"sku": "gel-002", "qty": 3}]}, token)
        assert resp.status_code == 200
        updated = _extract_content(_parse_mcp_result(resp))
        assert updated["version"] == 2
        assert updated["items"][0]["sku"] == "gel-002"
        assert updated["items"][0]["unit_minor"] == 24000


# --- Scope rejection tests ---


def test_create_cart_rejected_without_cart_write():
    app = create_app()
    with TestClient(app) as client:
        token = _get_token(["catalog:read"])
        resp = _mcp_call(client, "create_cart", {"items": [{"sku": "gel-001", "qty": 1}]}, token)
        assert resp.status_code == 200
        parsed = _parse_mcp_result(resp)
        assert parsed.get("result", {}).get("isError") is True


def test_search_rejected_without_catalog_read():
    app = create_app()
    with TestClient(app) as client:
        token = _get_token(["cart:write"])
        resp = _mcp_call(client, "search_products", {"query": "gelato"}, token)
        assert resp.status_code == 200
        parsed = _parse_mcp_result(resp)
        assert parsed.get("result", {}).get("isError") is True


# --- Audit log tests ---


def test_audit_log_records_tool_call():
    app = create_app()
    with TestClient(app) as client:
        token = _get_token(["catalog:read"])
        _mcp_call(client, "search_products", {"query": "mango"}, token)

        audit_resp = client.get("/admin/audit")
        assert audit_resp.status_code == 200
        assert "search_products" in audit_resp.text


def test_audit_log_records_rejected_call():
    app = create_app()
    with TestClient(app) as client:
        token = _get_token(["catalog:read"])
        _mcp_call(client, "create_cart", {"items": [{"sku": "gel-001", "qty": 1}]}, token)

        audit_resp = client.get("/admin/audit")
        assert audit_resp.status_code == 200
        assert "create_cart" in audit_resp.text
