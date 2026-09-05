# tests/stage10/test_multi_tenancy.py
# S10.1 — Second merchant (chai) via `openstore serve chai.yaml`: multi-tenancy by install.
#
# Proves, with zero code changes, that two merchant installs of the same package are
# fully isolated:
#   - separate DB per merchant (separate sqlite files / in-memory URLs)
#   - separate per-merchant ES256 PoAI keypair with namespaced `kid` (DECISIONS §11.1.10)
#   - chai's `/.well-known/poai-jwks.json` serves only chai keys
#   - the verifier selects by `kid` from a JWKS directory
# Cross-merchant test: one merchant's policies/budgets/bundles never leak into the other.

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openstore.config import (
    CampaignSettings,
    DatabaseConfig,
    DiscordConfig,
    LLMSettings,
    MerchantConfig,
    RazorpayConfig,
    Settings,
    WebAuthnConfig,
)
from openstore.core.database import init_database
from openstore.server import create_app

ROOT = Path(__file__).resolve().parents[2]
CHAI_YAML = ROOT / "chai.yaml"


def _settings(name: str, db_url: str, origin: str) -> Settings:
    return Settings(
        merchant=MerchantConfig(name=name),
        razorpay=RazorpayConfig(key_id="rzp_test_xxx", key_secret="s"),
        discord=DiscordConfig(
            bot_token="token", buyer_trace_channel_id=1,
            merchant_trace_channel_id=2, money_trace_channel_id=3,
            alerts_channel_id=4,
        ),
        webauthn=WebAuthnConfig(rp_id="localhost", rp_name="OpenStore", origin=origin),
        database=DatabaseConfig(url=db_url),
        llm=LLMSettings(),
        campaign=CampaignSettings(),
    )


def _clear_engine() -> None:
    import openstore.core.database as db_mod
    db_mod._engine = None


def _clear_poai_keys() -> None:
    import openstore.surfaces.wellknown as wk
    wk.POAI_KEYS = {}


def _clear_catalog_cache() -> None:
    import openstore.surfaces.catalog as cat
    cat.CATALOG_CACHE = None


def _merchant_id(name: str) -> str:
    return name.lower().replace(" ", "-").replace("'", "")


# ---------------------------------------------------------------------------
# Fixtures: two isolated installs
# ---------------------------------------------------------------------------


@pytest.fixture()
def gelato():
    _clear_engine()
    _clear_poai_keys()
    _clear_catalog_cache()
    cfg = _settings("Gelateria Milano", "sqlite://", "http://localhost:8000")
    init_database(cfg)
    app = create_app(cfg)
    yield cfg, TestClient(app)


@pytest.fixture()
def chai():
    _clear_engine()
    _clear_poai_keys()
    _clear_catalog_cache()
    cfg = _settings("Chai House", "sqlite://", "http://localhost:8001")
    init_database(cfg)
    app = create_app(cfg)
    yield cfg, TestClient(app)


# ---------------------------------------------------------------------------
# S10.1 — JWKS per merchant, key selected by kid
# ---------------------------------------------------------------------------


class TestPerMerchantPoaiKeys:
    def _write_jwks(self, tmp_path: Path, client: TestClient, filename: str) -> Path:
        r = client.get("/.well-known/poai-jwks.json")
        assert r.status_code == 200
        jwks = r.json()
        assert "keys" in jwks and jwks["keys"]
        out = tmp_path / filename
        out.write_text(json.dumps(jwks))
        return out

    def test_each_merchant_has_distinct_kid(self, gelato, chai):
        _, g = gelato
        _, c = chai
        g_kid = g.get("/.well-known/poai-jwks.json").json()["keys"][0]["kid"]
        c_kid = c.get("/.well-known/poai-jwks.json").json()["keys"][0]["kid"]
        assert g_kid != c_kid
        # namespaced per DECISIONS §11.1.10: "{merchant_id}-key-{n}"
        assert g_kid == "gelateria-milano-key-1"
        assert c_kid == "chai-house-key-1"

    def test_chai_jwks_serves_only_chai_keys(self, gelato, chai):
        _, g = gelato
        _, c = chai
        g_keys = g.get("/.well-known/poai-jwks.json").json()["keys"]
        c_keys = c.get("/.well-known/poai-jwks.json").json()["keys"]
        gelato_kids = {k["kid"] for k in g_keys}
        chai_kids = {k["kid"] for k in c_keys}
        assert "chai-house-key-1" in chai_kids
        assert "chai-house-key-1" not in gelato_kids
        assert "gelateria-milano-key-1" not in chai_kids

    def test_verifier_selects_by_kid_from_jwks_dir(
        self, tmp_path: Path, gelato, chai
    ):
        """The verifier's --merchant-jwks accepts a directory and selects by kid."""
        _, g = gelato
        _, c = chai
        self._write_jwks(tmp_path, g, "gelateria.json")
        self._write_jwks(tmp_path, c, "chai.json")

        from openstore.core.poai import create_poai_bundle
        from openstore.verify import checks

        wk = __import__("openstore.surfaces.wellknown", fromlist=["_load_or_generate_poai_keys"])
        chai_keys = wk._load_or_generate_poai_keys("chai-house")

        bundle = create_poai_bundle(
            transaction={"checkout_id": "chk_pref", "amount_minor": 15000},
            merchant_private_key_pem=chai_keys["private_key"],
            merchant_id="chai-house",
        )
        assert bundle["chain"]["merchant_signature"] is not None
        header = json.loads(__import__("base64").urlsafe_b64decode(
            bundle["chain"]["merchant_signature"].split(".")[0] + "=="))
        assert header["kid"] == "chai-house-key-1"

        ctx = checks.VerifierContext(bundle=bundle, jwks_dir=tmp_path)
        results = checks.run_all_checks(ctx)
        sig_result = next(r for r in results if r.name == "merchant_signature")
        assert sig_result.passed, sig_result.detail


# ---------------------------------------------------------------------------
# S10.1 — policies/budgets/bundles never leak across merchants
#
# Each merchant runs in its own SUBPROCESS (its own interpreter + own on-disk
# sqlite file), exactly like two `openstore serve` installs in production.
# A shared process/engine would mask isolation bugs, so we must not conflate
# the two merchants into one interpreter.
# ---------------------------------------------------------------------------

_SUBPROCESS = ROOT / "tests" / "stage10" / "_merchant_run.py"


def _run_merchant(db_path: Path, merchant_id: str, op: str, *args: str):
    import subprocess
    import sys
    proc = subprocess.run(
        [sys.executable, str(_SUBPROCESS), str(db_path), merchant_id, op, *args],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert proc.returncode == 0, f"merchant subprocess failed ({merchant_id} {op}): {proc.stderr}"
    return proc.stdout.strip()


class TestCrossMerchantIsolation:
    def test_policies_do_not_leak(self, tmp_path: Path):
        g_db = tmp_path / "gelateria.db"
        c_db = tmp_path / "chai.db"
        _run_merchant(g_db, "gelateria-milano", "seed_policy")
        _run_merchant(c_db, "chai-house", "seed_policy")

        # chai's install sees only chai's policy
        chai_rows = _run_merchant(c_db, "chai-house", "read_policies")
        assert "gelateria" not in chai_rows and "pol_chai" in chai_rows

        # gelateria's install sees only gelateria's policy
        gal_rows = _run_merchant(g_db, "gelateria-milano", "read_policies")
        assert "chai" not in gal_rows and "pol_gelateria" in gal_rows

    def test_budgets_and_bundles_do_not_leak(self, tmp_path: Path):
        g_db = tmp_path / "gelateria.db"
        c_db = tmp_path / "chai.db"
        # gelateria captures 40000 against its policy
        _run_merchant(g_db, "gelateria-milano", "seed_bundle")
        # chai has a policy but NO spend yet
        _run_merchant(c_db, "chai-house", "seed_policy")

        # chai's spend is 0 even though gelateria spent 40000 in its own install
        chai_spend = _run_merchant(c_db, "chai-house", "read_spend", "pol_chai")
        assert chai_spend == "0"

        # gelateria's own spend is 40000
        gal_spend = _run_merchant(g_db, "gelateria-milano", "read_spend", "pol_gelateria")
        assert gal_spend == "40000"

    def test_campaigns_do_not_leak(self, tmp_path: Path):

        g_db = tmp_path / "gelateria.db"
        c_db = tmp_path / "chai.db"
        # Seed a gelateria campaign by running a subprocess against the gelateria DB
        _run_merchant(g_db, "gelateria-milano", "seed_policy")

        # Seed a campaign directly in gelateria's install
        import subprocess
        import sys
        helper = f"""
import sys; sys.path.insert(0, {str(ROOT)!r} + '/src')
from openstore.config import Settings, MerchantConfig, RazorpayConfig, DiscordConfig, WebAuthnConfig, DatabaseConfig, LLMSettings, CampaignSettings
from openstore.core.database import get_session, init_database
from openstore.core.campaigns import create_campaign, activate_campaign
from datetime import UTC, datetime, timedelta
cfg = Settings(merchant=MerchantConfig(name='gelateria-milano'), razorpay=RazorpayConfig(key_id='x',key_secret='s'), discord=DiscordConfig(bot_token='t',buyer_trace_channel_id=1,merchant_trace_channel_id=2,money_trace_channel_id=3,alerts_channel_id=4), webauthn=WebAuthnConfig(rp_id='l',rp_name='O',origin='o'), database=DatabaseConfig(url='sqlite:///{g_db}'))
init_database(cfg); s=get_session(cfg)
now=datetime.now(UTC)
c=create_campaign(s,cfg,merchant_id='gelateria-milano',title='Gelato special',rationale='summer',discount_bps=1500,applies_to_skus=[],starts_at=now,ends_at=now+timedelta(days=7))
s.commit(); s.close()
"""
        subprocess.run([sys.executable, "-c", helper], cwd=ROOT, check=True, capture_output=True)

        # chai's install sees ZERO campaigns
        chai_rows = _run_merchant(c_db, "chai-house", "read_campaigns")
        assert chai_rows == "[]"

        # gelateria's install sees exactly one campaign, owned by gelateria
        gal_rows = _run_merchant(g_db, "gelateria-milano", "read_campaigns")
        assert "Gelato special" in gal_rows and "gelateria-milano" in gal_rows


# ---------------------------------------------------------------------------
# S10.1 — chai.yaml loads as a real second install via load_config
# ---------------------------------------------------------------------------


class TestChaiConfig:
    def test_chai_yaml_loads(self):
        """`openstore serve chai.yaml` must load the second merchant config."""
        from openstore.config import load_config
        assert CHAI_YAML.exists()
        cfg = load_config(CHAI_YAML)
        assert cfg.merchant.name == "Chai House"
        assert cfg.merchant.currency == "INR"
        # DECISIONS §11.1.10-friendly unique merchant id
        assert _merchant_id(cfg.merchant.name) == "chai-house"

    def test_chai_config_merchant_id_namespaced(self, chai):
        _, c = chai
        manifest = c.get("/.well-known/agent-commerce.json").json()
        assert manifest["merchant"]["id"] == "chai-house"
        assert manifest["merchant"]["name"] == "Chai House"
