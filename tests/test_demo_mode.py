# `openstore demo` — the credential-free path (demo_mode).
#
# The point of these tests is that demo mode swaps ONE thing (the PSP client,
# via _get_client) and fakes the authenticator hardware, while everything
# between stays the production code path. So the centrepiece is a full walk:
# cart -> policy signed with a real py_webauthn-verified assertion -> per-cart
# approval -> payment -> signed webhook -> RELEASED with an evidence bundle.
#
# The browser half (the shim replacing navigator.credentials) is simulated by
# calling /demo/webauthn/* directly, which is exactly what the shim's fetch
# does; the shim's own injection is asserted separately. Shared setup and the
# two ceremony helpers live in demo_harness.py; the fixture in conftest.py.

from __future__ import annotations

import pytest
from demo_harness import BUYER, approve_cart, enrol_and_sign_policy, write_config
from fastapi.testclient import TestClient
from openstore.core.database import apply_migrations
from openstore.server import create_app
from openstore.surfaces import demo as demo_surface


class TestGuards:
    def test_demo_mode_refuses_a_non_demo_key(self, tmp_path):
        """A demo config pointed at a real account would swallow real
        payments into an in-memory dict. Fail loud at boot."""
        config = write_config(tmp_path, key_id="rzp_test_real_account")
        with pytest.raises(ValueError, match="demo_mode requires"):
            create_app(config)

    def test_demo_routes_absent_without_demo_mode(self, tmp_path):
        import openstore.core.database as _db

        prev = _db._engine
        _db._engine = None
        config = write_config(tmp_path, key_id="rzp_test_real", demo=False)
        apply_migrations(config)
        try:
            with TestClient(create_app(config)) as client:
                assert client.get("/demo/shim.js").status_code == 404
                assert "/demo/shim.js" not in client.get("/chat").text
        finally:
            from openstore.core.database import get_engine

            get_engine(config).dispose()
            _db._engine = prev


class TestShimInjection:
    def test_html_pages_get_the_shim(self, demo_client):
        assert "/demo/shim.js" in demo_client.get("/chat").text

    def test_json_responses_are_untouched(self, demo_client):
        res = demo_client.get("/healthz")
        assert "shim" not in res.text
        assert res.json()["status"] in ("ok", "healthy")

    def test_shim_only_overrides_credentials(self, demo_client):
        body = demo_client.get("/demo/shim.js").text
        assert "navigator.credentials.create" in body
        assert "navigator.credentials.get" in body


class TestVirtualAuthenticator:
    def test_sign_count_advances_between_assertions(self, demo_client):
        """The RP rejects a counter that fails to advance, so a second
        approval in one sitting must not reuse the first count."""
        first = demo_client.post("/demo/webauthn/get", json={"challenge": "AAAA"}).json()
        second = demo_client.post("/demo/webauthn/get", json={"challenge": "BBBB"}).json()
        assert first["credential_id"] == second["credential_id"]
        assert first["authenticator_data"] != second["authenticator_data"]


class TestDemoPayment:
    def test_unknown_link_is_404(self, demo_client):
        assert demo_client.get("/demo/pay/plink_nope").status_code == 404

    def test_full_money_path_reaches_released_with_evidence(self, demo_client):
        # 1. A cart with no standing policy hands off to the studio.
        res = demo_client.post(
            "/web/cart",
            json={"buyer_key": BUYER, "items": [{"sku": "gelato_vanilla", "qty": 2}]},
        )
        assert res.json()["state"] == "signin"
        token = res.json()["signin_url"].split("token=")[1]

        # 2. Enrol the virtual passkey and sign the standing policy.
        enrol_and_sign_policy(demo_client, token)

        # 3. Same cart now reaches per-cart approval.
        res = demo_client.post(
            "/web/cart",
            json={"buyer_key": BUYER, "items": [{"sku": "gelato_vanilla", "qty": 2}]},
        )
        body = res.json()
        assert body["state"] == "approval"
        decision = approve_cart(
            demo_client, body["cart_id"], body["approval_url"].split("token=")[1]
        )
        shop = decision["shop_result"]
        assert decision["applied"] is True
        assert shop["aal_level"] == 2, "per-cart passkey tap should reach AAL2"
        checkout_id, short_url = shop["checkout_id"], shop["short_url"]

        # 4. The payment link points at the demo gateway, not Razorpay.
        assert "/demo/pay/" in short_url
        link_id = short_url.rsplit("/", 1)[-1]
        assert demo_client.get(f"/demo/pay/{link_id}").status_code == 200

        # 5. Paying emits a signed webhook through the real worker.
        assert demo_client.post(f"/demo/pay/{link_id}?action=paid").status_code == 200

        order = demo_client.get(f"/web/order/{checkout_id}?buyer_key={BUYER}").json()
        assert order["order_state"] == "RELEASED"
        assert order["evidence_url"] is not None, "the bundle is the point of the demo"

        bundle = demo_client.get(f"/orders/{checkout_id}/evidence?buyer_key={BUYER}").json()
        assert bundle["transaction"]["checkout_id"] == checkout_id
        assert bundle["transaction"]["amount_minor"] == shop["amount_minor"]

    def test_paying_twice_does_not_reprocess(self, demo_client):
        res = demo_client.post(
            "/web/cart",
            json={"buyer_key": BUYER, "items": [{"sku": "gelato_vanilla", "qty": 1}]},
        )
        enrol_and_sign_policy(demo_client, res.json()["signin_url"].split("token=")[1])
        res = demo_client.post(
            "/web/cart",
            json={"buyer_key": BUYER, "items": [{"sku": "gelato_vanilla", "qty": 1}]},
        )
        body = res.json()
        shop = approve_cart(demo_client, body["cart_id"], body["approval_url"].split("token=")[1])[
            "shop_result"
        ]
        link_id = shop["short_url"].rsplit("/", 1)[-1]

        demo_client.post(f"/demo/pay/{link_id}?action=paid")
        second = demo_client.post(f"/demo/pay/{link_id}?action=paid")
        assert second.status_code == 200
        assert "Paid" in second.text

        order = demo_client.get(f"/web/order/{shop['checkout_id']}?buyer_key={BUYER}").json()
        assert order["order_state"] == "RELEASED"


class TestDemoClient:
    def test_duplicate_reference_is_rejected_like_the_live_psp(self, tmp_path):
        """create_payment_link's recovery path keys on this exact rejection;
        a demo client that silently allowed duplicates would leave it dark."""
        from openstore.psp.demo_driver import DemoClient, DemoDuplicateReferenceError
        from openstore.psp.razorpay_driver import is_duplicate_reference_error

        demo_surface.reset()
        config = write_config(tmp_path)
        client = DemoClient(config)
        request = {"reference_id": "chk_x", "amount": 1000, "currency": "INR"}
        first = client.payment_link.create(request)
        with pytest.raises(DemoDuplicateReferenceError) as caught:
            client.payment_link.create(request)
        assert is_duplicate_reference_error(caught.value.code, str(caught.value))

        found = client.payment_link.all({"reference_id": "chk_x"})
        assert found["items"][0]["id"] == first["id"]
        demo_surface.reset()

    def test_short_url_points_at_the_demo_gateway(self, tmp_path):
        from openstore.psp.demo_driver import DemoClient

        demo_surface.reset()
        config = write_config(tmp_path)
        link = DemoClient(config).payment_link.create(
            {"reference_id": "chk_y", "amount": 2500, "currency": "INR"}
        )
        assert link["short_url"].endswith(f"/demo/pay/{link['id']}")
        assert link["status"] == "created"
        demo_surface.reset()
