"""§10 HTTP surface tests (IMPLEMENTATION_SPEC §8 / §9)."""

from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from openstore.models import Policy
from openstore.runtime import MerchantRuntime
from openstore.server import create_http_app

STATIC = Path(__file__).parent.parent / "openstore" / "static"


def _rt(tmp_path):
    return MerchantRuntime(
        merchant_signing_key=Ed25519PrivateKey.generate(),
        legal_name="Gelateria", country="IN",
        per_txn_limit=1_000_000_00, daily_limit=5_000_000_00,
        catalog={"GELATO": {"unit_price_paise": 5000, "tags": ["food"]}},
        policy=Policy(spend_limit_paise=1_000_000_00),
        rp_id="openstore.local", origin="https://openstore.local",
        ledger_url=f"sqlite:///{tmp_path / 'ledger.db'}",
    )


def _login(client):
    r = client.post("/admin/login", json={"password": "changeme"})
    assert r.status_code == 200


INTERNAL_GET = ["/intent/studio", "/admin/agents", "/internal/agents/sessions", "/internal/agents/rejections"]
INTERNAL_POST = ["/internal/policy/blast-radius", "/internal/webauthn/challenge"]


def test_no_internal_route_is_unauthenticated(tmp_path):
    client = TestClient(create_http_app(_rt(tmp_path)))
    for route in INTERNAL_GET:
        assert client.get(route).status_code in (401, 403), route
    for route in INTERNAL_POST:
        assert client.post(route, json={}).status_code in (401, 403), route
    # bearer-gated step-up begin
    assert client.post("/intent/step-up/begin", json={}).status_code in (401, 403)


def test_session_routes_require_csrf(tmp_path):
    client = TestClient(create_http_app(_rt(tmp_path)))
    _login(client)
    # POST without CSRF header must be rejected
    r = client.post("/internal/policy/blast-radius", json={})
    assert r.status_code == 403


def test_public_discovery_routes(tmp_path):
    client = TestClient(create_http_app(_rt(tmp_path)))
    assert client.get("/.well-known/poai-jwks.json").status_code == 200
    assert client.get("/.well-known/agent-policy.json").status_code == 200
    assert client.get("/agents").status_code == 200
    jwks = client.get("/.well-known/poai-jwks.json").json()
    assert jwks["kty"] == "EC" and jwks["alg"] == "ES256"


def test_blast_radius_requires_session(tmp_path):
    client = TestClient(create_http_app(_rt(tmp_path)))
    _login(client)
    # with CSRF cookie + header
    csrf = client.cookies.get("openstore_csrf")
    r = client.post("/internal/policy/blast-radius", json={},
                    headers={"x-csrf-token": csrf})
    assert r.status_code == 200
    body = r.json()
    assert "per_tx_worst_case_minor" in body
    assert "worst_case_total_exposure_minor" in body


def test_hold_cancel_is_unauthenticated_and_single_use(tmp_path):
    rt = _rt(tmp_path)
    client = TestClient(create_http_app(rt))
    hold = rt.create_hold("O1", 2)
    token = hold["cancel_token"]
    r = client.post(f"/hold/{token}/cancel")
    assert r.status_code == 200
    assert r.json()["status"] == "CANCELLED"
    # single-use: second attempt fails
    r2 = client.post(f"/hold/{token}/cancel")
    assert r2.status_code == 400


def test_viewer_is_self_contained(tmp_path):
    html = (STATIC / "viewer.html").read_text()
    # no external script/style/font loads
    assert "src=\"http" not in html
    assert "href=\"http" not in html
    # verifies in-browser with WebCrypto, no network
    assert "crypto.subtle" in html
    assert "verifyJws" in html
    # and the server serves it inline for an order
    client = TestClient(create_http_app(_rt(tmp_path)))
    # no bundle -> 404 page, but route exists
    assert client.get("/orders/nope/evidence/view").status_code == 404
