"""The app boots, answers health, and refuses the two dangerous configurations."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openstore.sidecar.app import app
from openstore.sidecar.core.codes import ReasonCode
from openstore.sidecar.core.settings import BootRefused, Settings, get_settings

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Never read the developer's real `.env`. A test whose result depends on an
    uncommitted file passes on one machine and fails on the next."""
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_reports_ready(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSTORE_MERCHANT_DOMAIN", "spoiledduckie.localhost")
    get_settings.cache_clear()
    response = client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["merchant_domain"] == "spoiledduckie.localhost"
    assert "warnings" not in body


def test_readyz_surfaces_the_dev_ssrf_allowlist(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SPEC §14 requires the exception to be visible in health output. An
    exception nobody can see is one that outlives its reason."""
    monkeypatch.setenv("OPENSTORE_DEV_PROFILE_HOSTS", "buyer-chat:3001")
    get_settings.cache_clear()
    body = client.get("/readyz").json()
    assert body["warnings"][0]["code"] == "dev-profile-allowlist-active"
    assert body["warnings"][0]["hosts"] == ["buyer-chat:3001"]


def test_no_interactive_docs_on_a_money_surface(client: TestClient) -> None:
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404


def test_codes_endpoint_serves_the_enum_not_a_copy(client: TestClient) -> None:
    served = client.get("/agentic/codes").json()["reason_codes"]
    assert served == [c.value for c in ReasonCode]


def test_live_keys_in_demo_mode_refuse_to_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_realmoney")
    monkeypatch.setenv("OPENSTORE_DEMO_MODE", "true")
    with pytest.raises(BootRefused, match="demo"):
        Settings()


def test_live_keys_with_dev_allowlist_refuse_to_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_realmoney")
    monkeypatch.setenv("OPENSTORE_DEMO_MODE", "false")
    monkeypatch.setenv("OPENSTORE_DEV_PROFILE_HOSTS", "buyer-chat:3001")
    with pytest.raises(BootRefused, match="SSRF"):
        Settings()


def test_test_keys_in_demo_mode_are_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_harmless")
    monkeypatch.setenv("OPENSTORE_DEMO_MODE", "true")
    assert Settings().has_live_provider_keys is False


def test_an_unrecognised_key_shape_counts_as_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guessing 'probably a test key' is how a real charge happens in a demo."""
    monkeypatch.setenv("RAZORPAY_KEY_ID", "something_else_entirely")
    monkeypatch.setenv("OPENSTORE_DEMO_MODE", "false")
    assert Settings().has_live_provider_keys is True


def test_a_cidr_in_the_dev_allowlist_refuses_to_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Named hosts only. A range is not an exception, it is a hole."""
    monkeypatch.setenv("OPENSTORE_DEV_PROFILE_HOSTS", "10.0.0.0/8")
    with pytest.raises(BootRefused, match="CIDR"):
        Settings()


def test_metadata_address_is_refused_even_in_the_dev_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSTORE_DEV_PROFILE_HOSTS", "169.254.169.254")
    with pytest.raises(BootRefused, match="metadata"):
        Settings()


def test_another_services_env_var_does_not_kill_the_sidecar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The compose demo shares one `.env` across three services (§10). The chat
    adding a variable must not stop the sidecar booting."""
    monkeypatch.setenv("CHAT_MODEL_DRIVER", "scripted")
    assert Settings().payment_provider == "fake"


#: Variables in `.env.example` that belong to another surface. The sidecar names
#: them in the template because the template is the one place a variable is
#: introduced (§10), and reads none of them.
_OTHER_SURFACES = {
    "CHAT_MODEL_DRIVER",
    "CHAT_DATABASE_PATH",
    "OLLAMA_BASE_URL",
    "OLLAMA_MODEL",
    "ANTHROPIC_API_KEY",
}


def test_env_example_and_settings_agree() -> None:
    """Both directions, because each half catches a different mistake: a field
    with no template entry is one an operator cannot set, and a template entry
    with no field is a typo that reads as an empty secret."""
    template = (REPO / ".env.example").read_text(encoding="utf-8")
    declared = {
        line.split("=", 1)[0].strip()
        for line in template.splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }
    fields = {name.upper() for name in Settings.model_fields}

    assert not (fields - declared), f"in Settings, not in .env.example: {sorted(fields - declared)}"
    assert not (
        declared - fields - _OTHER_SURFACES
    ), f"in .env.example, not in Settings: {sorted(declared - fields - _OTHER_SURFACES)}"
