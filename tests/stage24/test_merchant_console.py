# tests/stage24/test_merchant_console.py
# Stage 24 (Q-044/Q-045): merchant console, passkey login, evidence gating.
#
# Uses the full app (create_app) so route mounting, StaticFiles, and the
# session/CSRF wiring are exercised exactly as served.

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import openstore.core.database as _db_mod
import pytest
from fastapi.testclient import TestClient
from openstore.config import Settings
from openstore.core.database import get_session, init_database
from openstore.core.session import (
    SESSION_COOKIE,
    csrf_token_for,
    mint_session,
)
from openstore.models import Checkout
from openstore.server import create_app


def _settings(tmp_path: Path, name: str = "Test Merchant") -> Settings:
    from openstore.config import (
        CampaignSettings,
        DatabaseConfig,
        DiscordConfig,
        LLMSettings,
        MerchantConfig,
        RazorpayConfig,
        WebAuthnConfig,
    )

    cat = tmp_path / "catalog.yaml"
    cat.write_text(
        "items:\n"
        "  - sku: sku_a\n"
        "    name: Item A\n"
        "    unit_minor: 15000\n"
        "    tags: [demo]\n"
    )
    return Settings(
        merchant=MerchantConfig(name=name, currency="INR"),
        razorpay=RazorpayConfig(key_id="rzp_test_x", key_secret="s"),
        discord=DiscordConfig(
            bot_token="token", buyer_trace_channel_id=1,
            merchant_trace_channel_id=2, money_trace_channel_id=3, alerts_channel_id=4,
        ),
        webauthn=WebAuthnConfig(rp_id="localhost", rp_name="T", origin="http://localhost:8000"),
        # Per-test FILE database (never the shared in-memory one): seeded
        # checkouts/sessions must not leak into other modules' count
        # assertions (e.g. test_checkout_flow expects exactly 1 checkout).
        database=DatabaseConfig(url=f"sqlite:///{tmp_path}/stage24.db"),
        llm=LLMSettings(),
        campaign=CampaignSettings(),
        evidence_share_ttl_days=30,
        catalog_path=str(cat),
    )


@pytest.fixture()
def client(tmp_path):
    cfg = _settings(tmp_path)
    _db_mod._engine = None
    _db_mod._engine_url = None
    init_database(cfg)
    import openstore.surfaces.catalog as _cat

    _cat.CATALOG_CACHE = None
    app = create_app(cfg)
    c = TestClient(app, base_url="http://localhost:8000")
    c._openstore_config = cfg  # type: ignore[attr-defined]
    return c


def _authed(client: TestClient, operator: str = "merchant-1") -> str:
    """Mint a session directly and park its cookie on the client. Returns raw token."""
    cfg = client._openstore_config  # type: ignore[attr-defined]
    db = get_session(cfg)
    try:
        raw = mint_session(db, operator, "cred_x", None)
        db.commit()
    finally:
        db.close()
    client.cookies.set(SESSION_COOKIE, raw)
    return raw


def _csrf(raw: str) -> dict[str, str]:
    return {"X-OpenStore-CSRF": csrf_token_for(raw)}


def _register_via_http(client: TestClient, operator_id: str):
    """Perform the real /internal/webauthn/register begin+complete ceremony
    (mirrors claim()'s webauthnRegister() call), returning the enrolled
    VirtualAuthenticator. Unlike enrol_approver (direct DB insert), this
    exercises the exact HTTP sequence the browser claim flow uses."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from openstore.devtools.virtual_authenticator import VirtualAuthenticator, b64u_raw

    begin = client.post(
        "/internal/webauthn/register/begin",
        json={"user_name": operator_id, "display_name": "Merchant operator"},
        headers={"X-Operator-Id": operator_id},
    ).json()
    va = VirtualAuthenticator(
        credential_id=operator_id.encode().ljust(32, b"0")[:32],
        key=ec.generate_private_key(ec.SECP256R1()),
        rp_id=client._openstore_config.webauthn.rp_id,  # type: ignore[attr-defined]
        origin=client._openstore_config.webauthn.origin,  # type: ignore[attr-defined]
        sign_count=1,
    )
    reg = va.register(b64u_raw(begin["challenge"]))
    complete = client.post(
        "/internal/webauthn/register/complete",
        json={
            "credential_id": reg.credential_id,
            "client_data_json": reg.client_data_json,
            "attestation_object": reg.attestation_object,
            "challenge": begin["challenge"],
        },
        headers={"X-Operator-Id": operator_id},
    )
    assert complete.status_code == 200, complete.text
    return va


def _seed_evidence_checkout(cfg: Settings, buyer_key: str = "buyerkey12345678") -> str:
    db = get_session(cfg)
    try:
        now = datetime.now(UTC).replace(tzinfo=None)
        ck = Checkout(
            id="chk_ev1",
            trace_id="tr_ev1",
            client_id="cli_1",
            merchant_id="test-merchant",
            cart_hash="h1",
            cart_version=1,
            amount_minor=15000,
            currency="INR",
            state="RELEASED",
            aal_level=2,
            expires_at=now + timedelta(hours=1),
            idempotency_key="idem_ev1",
            cart_snapshot={"items": []},
            chat_platform="web",
            chat_user_id=buyer_key,
            poai_bundle={"proof": "bundle"},
        )
        db.add(ck)
        db.commit()
        return ck.id
    finally:
        db.close()


# ------------------------------------------------------------------ login
class TestLogin:
    def test_login_page_unclaimed_shows_claim(self, client: TestClient):
        r = client.get("/merchant/login")
        assert r.status_code == 200
        assert "Claim this store" in r.text

    def test_login_begin_complete_mints_session(self, client: TestClient, enrol_approver):
        cfg = client._openstore_config  # type: ignore[attr-defined]
        from openstore.devtools.virtual_authenticator import b64u_raw

        db = get_session(cfg)
        try:
            va = enrol_approver(db, cfg, user_handle="merchant-1")
        finally:
            db.close()
        # Page now offers sign-in, not claim.
        assert "Claim this store" not in client.get("/merchant/login").text

        begin = client.post(
            "/merchant/login", json={"action": "begin", "operator_id": "merchant-1"}
        )
        assert begin.status_code == 200, begin.text
        challenge = begin.json()["challenge"]
        asr = va.assert_credential(b64u_raw(challenge), sign_count=2)
        done = client.post(
            "/merchant/login",
            json={
                "action": "complete",
                "operator_id": "merchant-1",
                "credential_id": asr.credential_id,
                "client_data_json": asr.client_data_json,
                "authenticator_data": asr.authenticator_data,
                "signature": asr.signature,
                "challenge": challenge,
            },
        )
        assert done.status_code == 200, done.text
        assert done.json()["ok"] is True
        assert done.json()["csrf_token"]
        assert SESSION_COOKIE in done.cookies

        # The minted cookie opens the console.
        home = client.get("/merchant")
        assert home.status_code == 200
        assert "merchant-1" in home.text

    def test_complete_for_unclaimed_store_refused(self, client: TestClient):
        r = client.post(
            "/merchant/login",
            json={
                "action": "complete",
                "operator_id": "nobody",
                "credential_id": "c",
                "client_data_json": "x",
                "authenticator_data": "y",
                "signature": "z",
                "challenge": "ch",
            },
        )
        # The TOFU guard fires before the assertion is even evaluated.
        assert r.status_code == 401
        assert r.json()["detail"]["reason_code"] == "auth.session_required"
        assert SESSION_COOKIE not in r.cookies

    def test_stale_claim_rejected_with_409(self, client: TestClient, enrol_approver):
        cfg = client._openstore_config  # type: ignore[attr-defined]
        db = get_session(cfg)
        try:
            enrol_approver(db, cfg, user_handle="merchant-1")
        finally:
            db.close()
        r = client.post(
            "/merchant/login",
            json={"action": "complete", "claim": True, "operator_id": "merchant-1",
                  "credential_id": "c", "client_data_json": "x",
                  "authenticator_data": "y", "signature": "z", "challenge": "ch"},
        )
        # Fails on the assertion first (bad credential material) or on the
        # claim guard — either way it must NOT mint a session.
        assert r.status_code in (401, 409)
        assert SESSION_COOKIE not in r.cookies

    def test_claim_true_with_valid_assertion_after_claim_is_409(
        self, client: TestClient
    ):
        """Stale-claim race: op1 claimed first; op2's claim:true complete —
        backed by op2's OWN valid assertion — must 409, not silently mint
        operator #2. (Self-claim — op completing with claim:true when only
        op's own credential exists — proceeds to mint.)"""
        from cryptography.hazmat.primitives.asymmetric import ec
        from openstore.core.webauthn_rp import begin_registration, complete_registration
        from openstore.devtools.virtual_authenticator import (
            VirtualAuthenticator,
            b64u_raw,
        )

        cfg = client._openstore_config  # type: ignore[attr-defined]

        def _enrol(db, handle, cid):
            va = VirtualAuthenticator(
                credential_id=cid,
                key=ec.generate_private_key(ec.SECP256R1()),
                rp_id=cfg.webauthn.rp_id,
                origin=cfg.webauthn.origin,
                sign_count=1,
            )
            options = begin_registration(cfg, handle, handle, handle)
            reg = va.register(b64u_raw(options["challenge"]))
            complete_registration(
                session=db, config=cfg, user_handle=handle,
                credential_id=reg.credential_id,
                client_data_json=reg.client_data_json,
                attestation_object=reg.attestation_object,
                challenge_b64url=options["challenge"],
            )
            db.commit()
            return va

        db = get_session(cfg)
        try:
            _enrol(db, "op1", b"1" * 32)
            va2 = _enrol(db, "merchant-1", b"2" * 32)
        finally:
            db.close()

        # Self-claim proceeds: only op's own credential exists at claim time
        # in the ordinary flow; here op1 already claimed, so op2's claim 409s.
        begin = client.post(
            "/merchant/login", json={"action": "begin", "operator_id": "merchant-1"}
        ).json()
        asr = va2.assert_credential(b64u_raw(begin["challenge"]), sign_count=2)
        r = client.post(
            "/merchant/login",
            json={"action": "complete", "claim": True, "operator_id": "merchant-1",
                  "credential_id": asr.credential_id, "client_data_json": asr.client_data_json,
                  "authenticator_data": asr.authenticator_data, "signature": asr.signature,
                  "challenge": begin["challenge"]},
        )
        assert r.status_code == 409
        assert r.json()["detail"]["reason_code"] == "auth.store_already_claimed"
        assert SESSION_COOKIE not in r.cookies

    def test_first_time_claim_via_real_registration_mints_session(self, client: TestClient):
        """The actual browser claim() sequence: register a fresh passkey via
        HTTP (making store_claimed() true for THIS operator), then complete
        the claim login with that same credential. Must mint a session, not
        409 — regression test for a bug where the guard checked "any
        credential exists" instead of "a credential for another operator
        exists", making first-time claim impossible."""
        from openstore.devtools.virtual_authenticator import b64u_raw

        op = "merchant-1"
        va = _register_via_http(client, op)
        begin = client.post(
            "/merchant/login", json={"action": "begin", "operator_id": op}
        ).json()
        asr = va.assert_credential(b64u_raw(begin["challenge"]), sign_count=2)
        r = client.post(
            "/merchant/login",
            json={"action": "complete", "claim": True, "operator_id": op,
                  "credential_id": asr.credential_id, "client_data_json": asr.client_data_json,
                  "authenticator_data": asr.authenticator_data, "signature": asr.signature,
                  "challenge": begin["challenge"]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["operator_id"] == op
        assert SESSION_COOKIE in r.cookies

    def test_logout_revokes(self, client: TestClient):
        raw = _authed(client)
        assert client.get("/merchant").status_code == 200
        r = client.post("/merchant/logout", headers=_csrf(raw), json={})
        assert r.status_code == 200
        # Cookie revoked: console 401s even though the jar still holds it.
        assert client.get("/merchant").status_code == 401

    def test_ua_change_requires_reauth(self, client: TestClient):
        cfg = client._openstore_config  # type: ignore[attr-defined]
        db = get_session(cfg)
        try:
            raw = mint_session(db, "merchant-1", "cred_x", "UA-1")
            db.commit()
        finally:
            db.close()
        client.cookies.set(SESSION_COOKIE, raw)
        assert client.get("/merchant", headers={"user-agent": "UA-1"}).status_code == 200
        r = client.get("/merchant", headers={"user-agent": "UA-2"})
        assert r.status_code == 401
        assert r.json()["detail"]["reason_code"] == "auth.session_expired"


# ------------------------------------------------- DEF-2 / DEF-3 both ways
class TestOperatorResolution:
    PATHS = ["/intent/studio", "/campaign/studio", "/admin/orders/view"]

    def test_bare_navigation_401s(self, client: TestClient):
        for path in self.PATHS:
            assert client.get(path).status_code == 401, path

    def test_query_param_restores_decision_030(self, client: TestClient):
        for path in self.PATHS:
            r = client.get(path, params={"operator": "op_1"})
            assert r.status_code == 200, (path, r.status_code)

    def test_header_still_works(self, client: TestClient):
        for path in self.PATHS:
            r = client.get(path, headers={"X-Operator-Id": "op_1"})
            assert r.status_code == 200, (path, r.status_code)

    def test_session_cookie_opens_all_three(self, client: TestClient):
        _authed(client)
        for path in self.PATHS:
            assert client.get(path).status_code == 200, path

    def test_sentinel_no_html_route_401s_for_session_merchant(self, client: TestClient):
        """DEF-2 must not recur: every browser-navigation HTML route renders
        for a session-holding merchant."""
        _authed(client)
        for path in [
            "/",
            "/chat",
            "/intent/studio",
            "/campaign/studio",
            "/admin/orders/view",
            "/merchant",
            "/merchant/login",
            "/merchant/setup",
            "/merchant/catalog",
            "/merchant/orders",
            "/merchant/campaigns",
            "/merchant/policies",
            "/merchant/settings",
            "/merchant/merchandising",
        ]:
            r = client.get(path)
            assert r.status_code == 200, (path, r.status_code)


# -------------------------------------------------------------------- CSRF
class TestCsrf:
    def test_mutation_without_csrf_rejected(self, client: TestClient):
        raw = _authed(client)
        assert raw
        r = client.post("/merchant/settings", json={})
        assert r.status_code == 403
        assert r.json()["detail"]["reason_code"] == "auth.csrf_invalid"

    def test_mutation_with_wrong_csrf_rejected(self, client: TestClient):
        _authed(client)
        r = client.post(
            "/merchant/settings", json={}, headers={"X-OpenStore-CSRF": "wrong"}
        )
        assert r.status_code == 403

    def test_mutation_with_csrf_passes(self, client: TestClient):
        raw = _authed(client)
        r = client.post("/merchant/settings", json={}, headers=_csrf(raw))
        assert r.status_code == 200


# ---------------------------------------------------------------- settings
class TestSettings:
    def test_sid5_mismatch_refused_in_page(self, client: TestClient):
        """DEF-14: a rp_id/public_base_url mismatch is a readable 422 here,
        never a boot-time traceback later."""
        raw = _authed(client)
        r = client.post(
            "/merchant/settings",
            json={"webauthn_rp_id": "shop.example", "public_base_url": "https://other.example"},
            headers=_csrf(raw),
        )
        assert r.status_code == 422, r.text
        assert "SID-5" in r.json()["detail"]["message"]

    def test_matching_origin_pair_saves(self, client: TestClient):
        raw = _authed(client)
        r = client.post(
            "/merchant/settings",
            json={"campaign_max_active": 3, "evidence_share_ttl_days": 45},
            headers=_csrf(raw),
        )
        assert r.status_code == 200, r.text
        assert "campaign.max_active" in r.json()["saved"]

    def test_saved_catalog_source_is_actually_served(self, client: TestClient, tmp_path: Path):
        """Regression: /merchant/settings saving a catalog_source must reach
        load_catalog(config) — the same call every buyer-facing path and the
        CLI use — not just the Settings-page label. Before the fix,
        load_catalog(config) never consulted the DB overlay, so a merchant
        could "connect WooCommerce" and the storefront kept serving the old
        YAML file (or nothing) regardless."""
        from openstore.surfaces.catalog import load_catalog

        csv_path = tmp_path / "alt_catalog.csv"
        csv_path.write_text("sku,name,price\nALT-1,Alt Item,25.00\n")
        cfg = client._openstore_config  # type: ignore[attr-defined]

        raw = _authed(client)
        r = client.post(
            "/merchant/settings",
            json={"catalog_source": {"type": "csv", "path": str(csv_path)}},
            headers=_csrf(raw),
        )
        assert r.status_code == 200, r.text
        assert "catalog_source" in r.json()["saved"]

        items = load_catalog(cfg)
        assert [i["sku"] for i in items] == ["ALT-1"]

    def test_ttl_beyond_retention_refused(self, client: TestClient):
        raw = _authed(client)
        r = client.post(
            "/merchant/settings",
            json={"evidence_share_ttl_days": 541},
            headers=_csrf(raw),
        )
        assert r.status_code == 422

    def test_rename_locked_once_live(self, client: TestClient):
        cfg = client._openstore_config  # type: ignore[attr-defined]
        _seed_evidence_checkout(cfg)
        raw = _authed(client)
        r = client.post(
            "/merchant/settings",
            json={"merchant_name": "New Name"},
            headers=_csrf(raw),
        )
        assert r.status_code == 422
        assert "live store" in r.json()["detail"]["message"]


# ----------------------------------------------------------------- catalog
class TestCatalog:
    def test_add_update_delete_roundtrip(self, client: TestClient):
        raw = _authed(client)
        h = _csrf(raw)
        assert client.post(
            "/merchant/catalog",
            json={"action": "add", "sku": "ring_1", "name": "Ring", "unit_minor": 145000,
                  "tags": ["jewellery"]},
            headers=h,
        ).status_code == 200
        assert "ring_1" in client.get("/merchant/catalog").text
        assert client.post(
            "/merchant/catalog",
            json={"action": "update", "sku": "ring_1", "unit_minor": 150000},
            headers=h,
        ).status_code == 200
        assert client.post(
            "/merchant/catalog", json={"action": "delete", "sku": "ring_1"}, headers=h
        ).status_code == 200
        assert "ring_1" not in client.get("/merchant/catalog").text

    def test_zero_price_refused(self, client: TestClient):
        raw = _authed(client)
        r = client.post(
            "/merchant/catalog",
            json={"action": "add", "sku": "free", "unit_minor": 0},
            headers=_csrf(raw),
        )
        assert r.status_code == 422

    def test_unknown_sku_404s_closed_set(self, client: TestClient):
        raw = _authed(client)
        r = client.post(
            "/merchant/catalog", json={"action": "delete", "sku": "nope"}, headers=_csrf(raw)
        )
        assert r.status_code == 404
        assert r.json()["detail"]["reason_code"] == "catalog.sku_not_found"


# ---------------------------------------------------------------- evidence
class TestEvidenceGating:
    BUYER_KEY = "buyerkey12345678"

    def test_anonymous_json_is_401(self, client: TestClient):
        cfg = client._openstore_config  # type: ignore[attr-defined]
        cid = _seed_evidence_checkout(cfg, self.BUYER_KEY)
        r = client.get(f"/orders/{cid}/evidence")
        assert r.status_code == 401
        assert r.json()["detail"]["reason_code"] == "auth.session_required"

    def test_buyer_key_grants_and_stranger_403s(self, client: TestClient):
        cfg = client._openstore_config  # type: ignore[attr-defined]
        cid = _seed_evidence_checkout(cfg, self.BUYER_KEY)
        ok = client.get(f"/orders/{cid}/evidence", params={"buyer_key": self.BUYER_KEY})
        assert ok.status_code == 200
        assert ok.json() == {"proof": "bundle"}
        bad = client.get(f"/orders/{cid}/evidence", params={"buyer_key": "strangerkey123456"})
        assert bad.status_code == 403
        assert bad.json()["detail"]["reason_code"] == "checkout.not_owned"

    def test_share_mint_use_revoke(self, client: TestClient):
        cfg = client._openstore_config  # type: ignore[attr-defined]
        cid = _seed_evidence_checkout(cfg, self.BUYER_KEY)
        raw = _authed(client)
        minted = client.post(
            "/merchant/orders", json={"action": "share", "checkout_id": cid}, headers=_csrf(raw)
        )
        assert minted.status_code == 200, minted.text
        token = minted.json()["share_url"].split("?t=")[1]

        stranger = TestClient(client.app, base_url="http://localhost:8000")
        assert stranger.get(f"/orders/{cid}/evidence", params={"t": token}).status_code == 200
        view = stranger.get(f"/orders/{cid}/evidence/view", params={"t": token})
        assert view.status_code == 200
        # Autoload carries the token and names the failure state explicitly.
        assert f"/orders/{cid}/evidence" in view.text
        assert "Receipt unavailable" in view.text

        assert client.post(
            "/merchant/orders", json={"action": "revoke-share", "checkout_id": cid},
            headers=_csrf(raw),
        ).status_code == 200
        gone = stranger.get(f"/orders/{cid}/evidence", params={"t": token})
        assert gone.status_code == 404
        assert gone.json()["detail"]["reason_code"] == "checkout.evidence_not_found"

    def test_merchant_session_grants(self, client: TestClient):
        cfg = client._openstore_config  # type: ignore[attr-defined]
        cid = _seed_evidence_checkout(cfg, self.BUYER_KEY)
        _authed(client)
        assert client.get(f"/orders/{cid}/evidence").status_code == 200

    def test_view_renders_explicit_denial_state(self, client: TestClient):
        cfg = client._openstore_config  # type: ignore[attr-defined]
        cid = _seed_evidence_checkout(cfg, self.BUYER_KEY)
        view = client.get(f"/orders/{cid}/evidence/view", params={"t": "bogus"})
        # Gating itself is a fail-loud JSON denial, not a silent blank page…
        assert view.status_code == 404
        # …while the autoload path renders the explicit in-page state.
        good = TestClient(client.app, base_url="http://localhost:8000")
        raw = _authed(client)
        token = client.post(
            "/merchant/orders", json={"action": "share", "checkout_id": cid}, headers=_csrf(raw)
        ).json()["share_url"].split("?t=")[1]
        rendered = good.get(f"/orders/{cid}/evidence/view", params={"t": token})
        assert "Receipt unavailable" in rendered.text  # the !res.ok branch text


# --------------------------------------------------------------------- cli
class TestInitCleanup:
    def test_init_uses_slug_rp_name_unit_minor(self, tmp_path):
        from openstore.cli import app
        from typer.testing import CliRunner

        out = tmp_path / "out"
        res = CliRunner().invoke(
            app, ["init", "--merchant", "SpoiledDuckie", "--output", str(out)]
        )
        assert res.exit_code == 0, res.output
        cfg = out / "spoiledduckie.yaml"
        assert cfg.exists(), sorted(p.name for p in out.iterdir())
        import yaml as _yaml

        data = _yaml.safe_load(cfg.read_text())
        assert data["webauthn"]["rp_name"] == "SpoiledDuckie"
        template = (out / "catalog.yaml").read_text()
        assert "unit_minor" in template
        assert "price_minor" not in template
        assert "gelato" not in template.lower()


# -------------------------------------------------------------- migration
class TestMigration0011:
    def test_upgrade_head_on_file_db(self, tmp_path):
        from openstore.config import (
            CampaignSettings,
            DatabaseConfig,
            DiscordConfig,
            LLMSettings,
            MerchantConfig,
            RazorpayConfig,
            WebAuthnConfig,
        )
        from openstore.core.database import apply_migrations

        db_path = tmp_path / "m.db"
        cfg = Settings(
            merchant=MerchantConfig(name="M", currency="INR"),
            razorpay=RazorpayConfig(key_id="rzp_test_x", key_secret="s"),
            discord=DiscordConfig(
                bot_token="token", buyer_trace_channel_id=1,
                merchant_trace_channel_id=2, money_trace_channel_id=3, alerts_channel_id=4,
            ),
            webauthn=WebAuthnConfig(rp_id="localhost", rp_name="T", origin="http://localhost:8000"),
            database=DatabaseConfig(url=f"sqlite:///{db_path}"),
            llm=LLMSettings(),
            campaign=CampaignSettings(),
        evidence_share_ttl_days=30,
        )
        apply_migrations(cfg)  # must reach head incl. 0011 without error
        import sqlite3

        tables = {r[0] for r in sqlite3.connect(db_path).execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "merchant_sessions" in tables
        assert "merchant_settings" in tables
        cols = {r[1] for r in sqlite3.connect(db_path).execute("PRAGMA table_info(checkouts)")}
        assert "evidence_token_hash" in cols
        assert "evidence_token_expires_at" in cols


class TestSetupConnectAndTest:
    """Stage 25 DONE WHEN #4 (server side): /merchant/setup connects a
    candidate source and imports its catalog without touching Settings.
    Network-free cases only (CSV file, unknown type); live WooCommerce is
    exercised by the adapter fixture suite."""

    def test_csv_file_connects(self, client: TestClient, tmp_path):
        raw = _authed(client)
        csv_file = tmp_path / "probe.csv"
        csv_file.write_text(
            "sku,name,price,tags,stock\nring_1,Ring,1450.00,jewellery,1\n"
        )
        r = client.post(
            "/merchant/setup",
            json={"action": "test", "source": {"type": "csv", "path": str(csv_file)}},
            headers=_csrf(raw),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["adapter"] == "csv"
        assert body["item_count"] == 1

    def test_unknown_type_fails_closed_set(self, client: TestClient):
        raw = _authed(client)
        r = client.post(
            "/merchant/setup",
            json={"action": "test", "source": {"type": "unicommerce"}},
            headers=_csrf(raw),
        )
        assert r.status_code == 200
        assert r.json()["ok"] is False
        assert r.json()["reason_code"] == "catalog.adapter_not_configured"

    def test_requires_session_and_csrf(self, client: TestClient):
        assert client.post("/merchant/setup", json={"action": "test", "source": {}}).status_code == 401
        raw = _authed(client)
        assert client.post(
            "/merchant/setup", json={"action": "test", "source": {}}
        ).status_code == 403
        r = client.post(
            "/merchant/setup", json={"action": "test", "source": {}}, headers=_csrf(raw)
        )
        assert r.json()["ok"] is False
