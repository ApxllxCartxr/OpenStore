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


async def test_boot_starts_the_expiry_sweeper_when_there_is_a_merchant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The sweeper is the wiring that makes `expires_at` mean anything. It was
    a pure function nothing ever called, so an abandoned tap held Merchant stock
    until the process restarted."""
    import asyncio

    from openstore.sidecar.app import app, lifespan

    monkeypatch.setenv("OPENSTORE_MERCHANT_DOMAIN", "spoiledduckie.localhost")
    monkeypatch.setenv("TRAIT_BASE_URL", "http://merchant.internal")
    monkeypatch.setenv("TRAIT_HMAC_SECRET", "conformance-secret")
    monkeypatch.setenv("SIDECAR_SIGNING_KEY_PATH", str(tmp_path / "keys.json"))
    get_settings.cache_clear()

    before = {t.get_coro().__qualname__ for t in asyncio.all_tasks()}  # type: ignore[union-attr]
    async with lifespan(app):
        running = {t.get_coro().__qualname__ for t in asyncio.all_tasks()}  # type: ignore[union-attr]
        started = running - before
        assert any("run_forever" in name for name in started), started
    # And it is cancelled at shutdown rather than left running against a
    # disposed engine.
    after = {t.get_coro().__qualname__ for t in asyncio.all_tasks()}  # type: ignore[union-attr]
    assert not any("run_forever" in name for name in after - before)


async def test_boot_says_so_when_there_is_no_sweeper(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """No trait means nothing to release stock through. That is reported, not
    discovered later as orders that never expire."""
    import logging

    from openstore.sidecar.app import app, lifespan

    monkeypatch.setenv("OPENSTORE_MERCHANT_DOMAIN", "spoiledduckie.localhost")
    get_settings.cache_clear()
    with caplog.at_level(logging.WARNING, logger="openstore"):
        async with lifespan(app):
            pass
    assert any("NO SWEEPER" in record.message for record in caplog.records)


async def test_boot_wires_a_passkey_rp_the_approve_page_can_use(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`AuthorityKind.PASSKEY` was in the closed set from hour 0 with no module
    behind it, so the strongest claim the system can make was enum-level only."""
    from openstore.sidecar import checkout as flow
    from openstore.sidecar.app import app, lifespan
    from openstore.sidecar.console.approve import get_context as approve_context

    monkeypatch.setenv("OPENSTORE_MERCHANT_DOMAIN", "spoiledduckie.localhost")
    monkeypatch.setenv("OPENSTORE_PUBLIC_ORIGIN", "http://spoiledduckie.localhost")
    monkeypatch.setenv("SIDECAR_SIGNING_KEY_PATH", str(tmp_path / "keys.json"))
    get_settings.cache_clear()

    async with lifespan(app):
        rp = flow.get_context().passkey_rp
        assert rp is not None
        # The RP ID decides which passkeys exist, so it is configuration and
        # never a request header.
        assert rp.rp_id == "spoiledduckie.localhost"
        assert rp.origin == "http://spoiledduckie.localhost"
        assert approve_context().passkey_enabled is True


async def test_boot_offers_no_ceremony_when_the_merchant_has_not_enabled_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from openstore.sidecar import checkout as flow
    from openstore.sidecar.app import app, lifespan
    from openstore.sidecar.console.approve import get_context as approve_context
    from openstore.sidecar.core.codes import AuthorityKind
    from openstore.sidecar.gate.policy import Policy, set_current_policy

    monkeypatch.setenv("OPENSTORE_MERCHANT_DOMAIN", "spoiledduckie.localhost")
    monkeypatch.setenv("SIDECAR_SIGNING_KEY_PATH", str(tmp_path / "keys.json"))
    get_settings.cache_clear()

    without = Policy(
        enabled_authority_kinds=frozenset(
            k for k in Policy().enabled_authority_kinds if k is not AuthorityKind.PASSKEY
        )
    )
    # Set the live Policy rather than patching the constructor. Boot no longer
    # builds a Policy of its own — it reads the one holder every surface reads,
    # so a patched `Policy` name is now a constructor nothing calls.
    set_current_policy(without)
    async with lifespan(app):
        assert flow.get_context().passkey_rp is None
        assert approve_context().passkey_enabled is False


# ── The key export, which was a setting that reached nothing ─────────────────


def test_a_new_key_is_exported_where_the_deploy_asked(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`SIDECAR_KEY_EXPORT_PATH` was declared, documented and wired to nothing —
    so a deploy could set it, believe it had a backup, and have none."""
    import logging

    from openstore.sidecar.app import _export_new_keys
    from openstore.sidecar.core.settings import Settings
    from openstore.sidecar.evidence.keys import Keyring

    keyring = Keyring(merchant_domain="shop.test")
    keyring.enroll("k1")
    export = tmp_path / "backup" / "keys.json"

    settings = Settings(
        sidecar_signing_key_path=str(tmp_path / "live.json"),
        sidecar_key_export_path=str(export),
    )
    _export_new_keys(settings, keyring, logging.getLogger("test"))

    assert export.exists(), "the export path was set and nothing was written to it"
    from openstore.sidecar.evidence.cli import main

    assert main(["check", str(export)]) == 0


def test_no_export_path_is_a_loud_warning_not_a_silent_skip(
    tmp_path: Path,
    caplog,  # type: ignore[no-untyped-def]
) -> None:
    import logging

    from openstore.sidecar.app import _export_new_keys
    from openstore.sidecar.core.settings import Settings
    from openstore.sidecar.evidence.keys import Keyring

    keyring = Keyring(merchant_domain="shop.test")
    keyring.enroll("k1")

    with caplog.at_level(logging.WARNING):
        _export_new_keys(Settings(), keyring, logging.getLogger("openstore"))

    assert any("NO KEY EXPORT" in record.message for record in caplog.records)
