"""Configuration that reaches nothing is configuration that is not there.

Every failure this file covers had the same shape: a setting was declared, was
reported in health output or a console banner, was covered by a unit test that
constructed the object by hand — and was never passed to the object that
enforces it at runtime. Each one passed every suite and was false in the running
system.

These tests assert the wiring itself, which is the part no unit test of a
component can see.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from openstore.sidecar.core.settings import get_settings
from openstore.sidecar.protocols.agent_routes import AgentSurface
from openstore.sidecar.protocols.agent_routes import configure as configure_surface


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    configure_surface(AgentSurface())


async def _boot(monkeypatch: pytest.MonkeyPatch, **env: str) -> AgentSurface:
    """Run the real lifespan, then read the surface it actually configured."""
    from openstore.sidecar.app import app, lifespan
    from openstore.sidecar.protocols.agent_routes import get_surface

    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    async with lifespan(app):
        return get_surface()


async def test_boot_enrolls_and_publishes_a_signing_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The sidecar served an empty JWKS for its whole life because nothing ever
    built a Keyring outside a test."""
    surface = await _boot(
        monkeypatch,
        OPENSTORE_MERCHANT_DOMAIN="spoiledduckie.localhost",
        SIDECAR_SIGNING_KEY_PATH=str(tmp_path / "keys.json"),
    )
    assert surface.jwks["keys"], "booted with no signing key"
    assert surface.keyring is not None
    assert surface.keyring.current.kid == "k1"


async def test_a_restart_publishes_the_same_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = str(tmp_path / "keys.json")
    first = await _boot(
        monkeypatch, OPENSTORE_MERCHANT_DOMAIN="shop.test", SIDECAR_SIGNING_KEY_PATH=path
    )
    published = first.jwks
    second = await _boot(
        monkeypatch, OPENSTORE_MERCHANT_DOMAIN="shop.test", SIDECAR_SIGNING_KEY_PATH=path
    )
    assert second.jwks == published


async def test_the_ssrf_allowlist_reaches_the_fetcher_that_enforces_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It was reported in `/readyz` and the console banner while the fetcher held
    an empty tuple, so the named host was refused and no agent could register."""
    surface = await _boot(monkeypatch, OPENSTORE_DEV_PROFILE_HOSTS="buyer-chat:3001")
    assert surface.fetcher.dev_hosts == ("buyer-chat:3001",)


async def test_oauth_credentials_reach_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    surface = await _boot(
        monkeypatch, OAUTH_CLIENT_ID="demo-agent", OAUTH_CLIENT_SECRET="demo-agent-secret"
    )
    assert surface.admission.clients == {"demo-agent": "demo-agent-secret"}
    assert surface.admission.issue_for_client("demo-agent", "demo-agent-secret").token


async def test_the_public_origin_reaches_the_card(monkeypatch: pytest.MonkeyPatch) -> None:
    """A card advertising `https://` on a plain-http deploy hands an agent seven
    endpoints that do not answer."""
    surface = await _boot(
        monkeypatch,
        OPENSTORE_MERCHANT_DOMAIN="spoiledduckie.localhost",
        OPENSTORE_PUBLIC_ORIGIN="http://spoiledduckie.localhost",
    )
    assert surface.public_origin == "http://spoiledduckie.localhost"
