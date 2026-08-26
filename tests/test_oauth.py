import base64
import hashlib
import secrets
from fastapi.testclient import TestClient
from merchant.app import app

client = TestClient(app)


def test_full_pkce_dance_issues_scoped_token():
    reg = client.post("/oauth/register", json={
        "client_name": "pytest-client",
        "redirect_uris": ["http://127.0.0.1:8765/callback"],
    })
    client_id = reg.json()["client_id"]

    verifier = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()

    consent = client.post("/oauth/consent", data={
        "approve": "yes",
        "client_id": client_id,
        "scope": "catalog:read cart:write",
        "redirect_uri": "http://127.0.0.1:8765/callback",
        "code_challenge": challenge,
        "state": "xyz",
    }, follow_redirects=False)
    location = consent.headers["location"]
    code = location.split("code=")[1].split("&")[0]

    token_resp = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": "http://127.0.0.1:8765/callback",
        "client_id": client_id,
    })
    body = token_resp.json()
    assert "access_token" in body
    assert "catalog:read" in body["scope"]
    assert "cart:write" in body["scope"]
