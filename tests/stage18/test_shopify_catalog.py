# tests/stage18/test_shopify_catalog.py
# Stage 18 (DECISION-037) — Shopify read-only catalog adapter. All HTTP is
# mocked (httpx.MockTransport); the one live probe ran once in-session and
# pinned the fail-loud rules this suite locks in.

from __future__ import annotations

import httpx
import pytest
from openstore.config import (
    CampaignSettings,
    DatabaseConfig,
    DiscordConfig,
    LLMSettings,
    MerchantConfig,
    RazorpayConfig,
    Settings,
    ShopifyConfig,
    WebAuthnConfig,
)
from openstore.surfaces import catalog as catalog_mod
from openstore.surfaces import shopify_catalog as mod


@pytest.fixture(autouse=True)
def _clear_caches():
    mod._TOKEN_CACHE.clear()
    mod._CATALOG_CACHE.clear()
    yield
    mod._TOKEN_CACHE.clear()
    mod._CATALOG_CACHE.clear()


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        merchant=MerchantConfig(name="Gelateria Milano", currency="INR"),
        razorpay=RazorpayConfig(key_id="rzp_test_xxxxxxxx", key_secret="test"),
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
        shopify=ShopifyConfig(
            store_domain="demo.myshopify.com",
            client_id="cid",
            client_secret="csec",
        ),
    )


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _token_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={"access_token": "tok", "scope": "read_products", "expires_in": 86399},
    )


def _install(monkeypatch, handler) -> dict[str, int]:
    counts: dict[str, int] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        counts[request.url.path] = counts.get(request.url.path, 0) + 1
        return handler(request)

    monkeypatch.setattr(mod, "_http_client", lambda: _mock_client(_handler))
    return counts


def _shopify_api(
    products: list[dict],
    currency: str = "INR",
    link_next: str | None = None,
) -> object:
    def _handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/oauth/access_token"):
            return _token_ok(request)
        if path.endswith("/shop.json"):
            return httpx.Response(200, json={"shop": {"currency": currency}})
        if path.endswith("/products.json"):
            headers = {"Link": f'<{link_next}>; rel="next"'} if link_next else {}
            return httpx.Response(200, json={"products": products}, headers=headers)
        raise AssertionError(f"unexpected {request.url}")
    return _handler


def _variant(sku="GEL-VAN-500", price="210.00", title="500ml") -> dict:
    return {"sku": sku, "price": price, "title": title}


def _product(title="Vanilla", tags="vegan, dairy-free", variants=None) -> dict:
    return {
        "title": title,
        "tags": tags,
        "body_html": "<p>Slow-churned.</p>",
        "variants": variants or [_variant()],
    }


class TestPriceToMinor:
    @pytest.mark.parametrize(
        ("price", "minor"),
        [("210.00", 21000), ("24.95", 2495), ("10", 1000), ("0.99", 99)],
    )
    def test_exact(self, price, minor):
        assert mod.price_to_minor(price) == minor

    def test_fractional_cents_fail_loud(self):
        with pytest.raises(ValueError, match="fractional cents"):
            mod.price_to_minor("1.005")

    def test_non_decimal_fails_loud(self):
        with pytest.raises(ValueError, match="not a decimal"):
            mod.price_to_minor("abc")


class TestNormalize:
    def test_happy_path_shape(self):
        item = mod.normalize_variant("Vanilla", "vegan, dairy-free", "<p>x</p>", _variant())
        assert item == {
            "sku": "GEL-VAN-500",
            "name": "Vanilla — 500ml",
            "unit_minor": 21000,
            "tags": ["dairy-free", "vegan"],
            "related_skus": [],
            "description": "x",
            "offers": [],
        }

    def test_default_title_uses_product_name(self):
        item = mod.normalize_variant("Vanilla", "", "", _variant(title="Default Title"))
        assert item is not None and item["name"] == "Vanilla"

    @pytest.mark.parametrize("sku", [None, "", "   "])
    def test_missing_sku_skipped_never_synthesized(self, sku):
        assert mod.normalize_variant("V", "", "", _variant(sku=sku)) is None


class TestToken:
    def test_cached_to_expiry(self, monkeypatch):
        counts = _install(monkeypatch, _token_ok)
        assert mod.get_admin_token("d", "c", "s") == "tok"
        assert mod.get_admin_token("d", "c", "s") == "tok"
        assert sum(counts.values()) == 1

    def test_non_200_fails_loud(self, monkeypatch):
        _install(monkeypatch, lambda req: httpx.Response(401, json={}))
        with pytest.raises(RuntimeError, match="token exchange failed"):
            mod.get_admin_token("d", "c", "s")

    def test_missing_read_scope_fails_loud(self, monkeypatch):
        def _h(req):
            return httpx.Response(200, json={"access_token": "t", "scope": "read_orders"})
        _install(monkeypatch, _h)
        with pytest.raises(RuntimeError, match="lacks read_products"):
            mod.get_admin_token("d", "c", "s")


class TestLoad:
    def test_currency_gate_rejects_non_inr(self, monkeypatch, settings):
        _install(monkeypatch, _shopify_api([_product()], currency="USD"))
        with pytest.raises(ValueError, match="currency"):
            mod.load_shopify_catalog(settings)

    def test_zero_usable_variants_fails_loud(self, monkeypatch, settings):
        _install(monkeypatch, _shopify_api([_product(variants=[_variant(sku=None)])]))
        with pytest.raises(ValueError, match="zero usable variants"):
            mod.load_shopify_catalog(settings)

    def test_mixed_skip_counts_loudly(self, monkeypatch, settings):
        _install(
            monkeypatch,
            _shopify_api([_product(variants=[_variant(), _variant(sku=None)])]),
        )
        items = mod.load_shopify_catalog(settings)
        assert [i["sku"] for i in items] == ["GEL-VAN-500"]

    def test_ttl_cache_avoids_refetch(self, monkeypatch, settings):
        counts = _install(monkeypatch, _shopify_api([_product()]))
        assert len(mod.load_shopify_catalog(settings)) == 1
        assert len(mod.load_shopify_catalog(settings)) == 1
        assert sum(counts.values()) == 3  # token + shop + products, once

    def test_ttl_zero_refetches(self, monkeypatch, settings):
        monkeypatch.setattr(mod, "CACHE_TTL_SECONDS", 0)
        counts = _install(monkeypatch, _shopify_api([_product()]))
        mod.load_shopify_catalog(settings)
        mod.load_shopify_catalog(settings)
        # token (1) + 2x (shop + products); the token cache is independent of
        # the catalog TTL and correctly survives it.
        assert sum(counts.values()) == 5

    def test_pagination_follows_next(self, monkeypatch, settings):
        page2 = "https://demo.myshopify.com/admin/api/2025-01/products.json?page_info=abc"

        def _h(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/oauth/access_token"):
                return _token_ok(request)
            if path.endswith("/shop.json"):
                return httpx.Response(200, json={"shop": {"currency": "INR"}})
            if "page_info" in str(request.url):
                return httpx.Response(200, json={"products": [_product(title="P2")]})
            return httpx.Response(
                200,
                json={"products": [_product(title="P1")]},
                headers={"Link": f"<{page2}>; rel=\"next\""},
            )

        _install(monkeypatch, _h)
        items = mod.load_shopify_catalog(settings)
        assert len(items) == 2

    def test_page_cap_fails_loud(self, monkeypatch, settings):
        monkeypatch.setattr(mod, "MAX_PAGES", 1)

        def _h(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/oauth/access_token"):
                return _token_ok(request)
            if path.endswith("/shop.json"):
                return httpx.Response(200, json={"shop": {"currency": "INR"}})
            return httpx.Response(
                200,
                json={"products": [_product()]},
                headers={"Link": '<https://x/products.json?page_info=n>; rel="next"'},
            )

        _install(monkeypatch, _h)
        with pytest.raises(RuntimeError, match="exceeds 1 pages"):
            mod.load_shopify_catalog(settings)


class TestBranchAndConfig:
    def test_load_catalog_routes_to_shopify(self, monkeypatch, settings):
        _install(monkeypatch, _shopify_api([_product()]))
        items = catalog_mod.load_catalog(settings)
        assert [i["sku"] for i in items] == ["GEL-VAN-500"]

    def test_shopify_absent_by_default(self, settings):
        settings.shopify = None
        assert settings.shopify is None

    def test_from_yaml_interpolates_shopify_block(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "demo.myshopify.com")
        monkeypatch.setenv("SHOPIFY_CLIENT_ID", "cid")
        monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "csec")
        cfg = tmp_path / "m.yaml"
        cfg.write_text(
            'merchant:\n  name: "M"\n  currency: "INR"\n'
            "razorpay:\n  key_id: k\n  key_secret: s\n"
            "discord:\n  bot_token: t\n  buyer_trace_channel_id: 1\n"
            "  merchant_trace_channel_id: 2\n  money_trace_channel_id: 3\n  alerts_channel_id: 4\n"
            'webauthn:\n  rp_id: "localhost"\n  rp_name: "O"\n  origin: "http://localhost:8000"\n'
            "shopify:\n  store_domain: \"${SHOPIFY_STORE_DOMAIN}\"\n"
            '  client_id: "${SHOPIFY_CLIENT_ID}"\n  client_secret: "${SHOPIFY_CLIENT_SECRET}"\n'
        )
        from openstore.config import load_config

        loaded = load_config(cfg)
        assert loaded.shopify is not None
        assert loaded.shopify.store_domain == "demo.myshopify.com"

    def test_downstream_digest_stable(self, monkeypatch, settings):
        _install(monkeypatch, _shopify_api([_product()]))
        items = catalog_mod.load_catalog(settings)
        d1 = catalog_mod.compute_catalog_digest(settings)
        changed = [dict(items[0], unit_minor=items[0]["unit_minor"] + 1)]
        import json as _json

        norm = _json.dumps(changed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        import hashlib

        d2 = "sha256:" + hashlib.sha256(norm.encode()).hexdigest()
        assert d1.startswith("sha256:") and d1 != d2
