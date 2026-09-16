# First-run wizard (setup_wizard.py). Prompts are driven by patching
# Console.input — the single seam every rich Prompt/Confirm funnels through,
# including password prompts (getpass would otherwise read the real tty).

from __future__ import annotations

from collections import deque
from typing import Any

import pytest
import yaml
from openstore.config import Settings
from openstore.setup_wizard import (
    WizardState,
    config_from_state,
    env_from_state,
    render_config,
    render_env,
    run_wizard,
)
from rich.console import Console


@pytest.fixture
def answers(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Queue answers; returns the queue so a test can append mid-run."""
    queued: deque[str] = deque()

    def fake_input(self: Console, prompt: Any = "", **kwargs: Any) -> str:
        if not queued:
            raise AssertionError("wizard asked for more input than the test queued")
        return queued.popleft()

    monkeypatch.setattr(Console, "input", fake_input)
    return queued


def _console() -> Console:
    return Console(force_terminal=False, width=100)


# Local deployment, YAML catalog, no Discord, SQLite.
BASELINE = [
    "Gelateria Toscana",  # merchant
    "",  # currency -> INR default
    "3",  # deployment: local
    "rzp_test_abc",  # razorpay key id
    "secret_value",  # razorpay key secret
    "",  # webhook secret skipped
    "1",  # catalog: yaml
    "n",  # discord off
    "1",  # database: sqlite
    "y",  # review confirmed
]


class TestHappyPath:
    def test_yaml_local_run_produces_loadable_config(self, answers, tmp_path, monkeypatch):
        answers.extend(BASELINE)
        state = run_wizard(_console(), WizardState())

        assert state.merchant == "Gelateria Toscana"
        assert state.slug == "gelateria-toscana"
        assert state.currency == "INR"
        assert state.discord_enabled is False
        assert state.source_kind == "yaml"

        cfg = config_from_state(state)
        env = env_from_state(state)
        path = tmp_path / "gelateria-toscana.yaml"
        path.write_text(render_config(state, cfg))
        (tmp_path / "catalog.yaml").write_text("items: []\n")
        for name, value in env.items():
            monkeypatch.setenv(name, value)

        settings = Settings.from_yaml(path)
        assert settings.merchant.name == "Gelateria Toscana"
        assert settings.razorpay.key_id == "rzp_test_abc"
        assert settings.discord.buyer_bot_enabled is False

    def test_secrets_reach_env_not_yaml(self, answers):
        answers.extend(BASELINE)
        state = run_wizard(_console(), WizardState())

        text = render_config(state, config_from_state(state))
        assert "secret_value" not in text
        assert "${RAZORPAY_KEY_SECRET}" in text
        assert "RAZORPAY_KEY_SECRET=secret_value" in render_env(env_from_state(state))

    def test_yaml_source_omits_catalog_source_block(self, answers):
        """load_config only defaults catalog_path when no source is set, so a
        YAML catalog must write no catalog_source at all."""
        answers.extend(BASELINE)
        state = run_wizard(_console(), WizardState())
        assert "catalog_source" not in config_from_state(state)

    def test_skipped_webhook_secret_is_absent(self, answers):
        answers.extend(BASELINE)
        state = run_wizard(_console(), WizardState())
        assert "webhook_secret" not in config_from_state(state)["razorpay"]


class TestWebAuthnDerivation:
    def test_subdomain_origin_drives_rp_id(self, answers):
        answers.extend(
            [
                "Shop",
                "",
                "2",  # subdomain
                "https://openstore.myshop.example",
                "rzp_test_abc",
                "s",
                "",
                "1",
                "n",
                "1",
                "y",
            ]
        )
        state = run_wizard(_console(), WizardState())
        cfg = config_from_state(state)
        assert cfg["webauthn"] == {
            "rp_id": "openstore.myshop.example",
            "rp_name": "Shop",
            "origin": "https://openstore.myshop.example",
        }
        assert cfg["public_base_url"] == "https://openstore.myshop.example"

    def test_local_deployment_writes_no_public_base_url(self, answers):
        answers.extend(BASELINE)
        state = run_wizard(_console(), WizardState())
        cfg = config_from_state(state)
        assert "public_base_url" not in cfg
        assert cfg["webauthn"]["rp_id"] == "localhost"

    def test_malformed_origin_is_refused_then_retried(self, answers):
        answers.extend(
            [
                "Shop",
                "",
                "2",
                "myshop.example",  # no scheme -> refused
                "https://myshop.example",
                "rzp_test_abc",
                "s",
                "",
                "1",
                "n",
                "1",
                "y",
            ]
        )
        state = run_wizard(_console(), WizardState())
        assert state.public_base_url == "https://myshop.example"


class TestNavigation:
    def test_back_returns_to_the_previous_step(self, answers):
        answers.extend(
            [
                "Wrong Name",
                "",
                ":back",  # from deployment, back to merchant
                "Right Name",
                "",
                "3",
                "rzp_test_abc",
                "s",
                "",
                "1",
                "n",
                "1",
                "y",
            ]
        )
        state = run_wizard(_console(), WizardState())
        assert state.merchant == "Right Name"

    def test_declining_review_reopens_the_last_step(self, answers):
        url = "postgresql+psycopg://openstore:pw@db:5432/openstore"
        answers.extend(BASELINE[:-1] + ["n", "2", url, "y"])
        state = run_wizard(_console(), WizardState())
        # Review sent us back to Database; picking 2 asks for a Postgres URL.
        assert state.database_url == url

    def test_required_field_is_reasked_when_blank(self, answers):
        answers.extend([""] + BASELINE)
        state = run_wizard(_console(), WizardState())
        assert state.merchant == "Gelateria Toscana"


class TestPlatformSource:
    def test_verified_source_writes_env_refs(self, answers, monkeypatch):
        """A connecting source is persisted with ${VAR} refs, credentials
        in .env. The connection test itself is stubbed — adapter behavior is
        covered in tests/stage25."""
        tested: dict[str, Any] = {}

        def fake_test(console: Console, raw: dict[str, Any]) -> bool:
            tested.update(raw)
            return True

        monkeypatch.setattr("openstore.setup_wizard._test_source", fake_test)
        answers.extend(
            [
                "Shop",
                "",
                "3",
                "rzp_test_abc",
                "s",
                "",
                "2",  # woocommerce
                "https://shop.example.com",
                "ck_live",
                "cs_live",
                "n",
                "1",
                "y",
            ]
        )
        state = run_wizard(_console(), WizardState())

        # The test saw real credentials...
        assert tested["consumer_key"] == "ck_live"
        # ...and the YAML never does.
        cfg = config_from_state(state)
        assert cfg["catalog_source"] == {
            "type": "woocommerce",
            "base_url": "https://shop.example.com",
            "consumer_key": "${WOOCOMMERCE_CONSUMER_KEY}",
            "consumer_secret": "${WOOCOMMERCE_CONSUMER_SECRET}",
        }
        env = env_from_state(state)
        assert env["WOOCOMMERCE_CONSUMER_KEY"] == "ck_live"
        assert env["WOOCOMMERCE_CONSUMER_SECRET"] == "cs_live"
        assert state.source_verified is True

    def test_failed_connection_retries_then_saves_unverified(self, answers, monkeypatch):
        monkeypatch.setattr(
            "openstore.setup_wizard._test_source",
            lambda console, raw: False,
        )
        answers.extend(
            [
                "Shop",
                "",
                "3",
                "rzp_test_abc",
                "s",
                "",
                "2",
                "https://shop.example.com",
                "ck",
                "cs",
                "n",  # don't retry -> save unverified
                "n",  # discord
                "1",
                "y",
            ]
        )
        state = run_wizard(_console(), WizardState())
        assert state.source_verified is False
        assert config_from_state(state)["catalog_source"]["type"] == "woocommerce"

    def test_csv_without_url_or_path_is_refused(self, answers, monkeypatch):
        monkeypatch.setattr("openstore.setup_wizard._test_source", lambda console, raw: True)
        answers.extend(
            [
                "Shop",
                "",
                "3",
                "rzp_test_abc",
                "s",
                "",
                "8",  # csv
                "",  # no url
                "",  # no path -> refused, re-asked
                "https://example.com/items.csv",
                "",
                "n",
                "1",
                "y",
            ]
        )
        state = run_wizard(_console(), WizardState())
        assert state.source["url"] == "https://example.com/items.csv"


class TestDiscord:
    def test_enabled_discord_collects_channels(self, answers):
        answers.extend(
            [
                "Shop",
                "",
                "3",
                "rzp_test_abc",
                "s",
                "",
                "1",
                "y",  # discord on
                "bot-token",
                "1",
                "2",
                "3",
                "4",
                "1",
                "y",
            ]
        )
        state = run_wizard(_console(), WizardState())
        cfg = config_from_state(state)
        assert cfg["discord"]["bot_token"] == "${DISCORD_BOT_TOKEN}"
        assert cfg["discord"]["alerts_channel_id"] == 4
        assert env_from_state(state)["DISCORD_BOT_TOKEN"] == "bot-token"

    def test_disabled_discord_writes_no_invented_channel_ids(self, answers):
        answers.extend(BASELINE)
        state = run_wizard(_console(), WizardState())
        cfg = config_from_state(state)
        assert cfg["discord"]["buyer_bot_enabled"] is False
        assert set(cfg["discord"].values()) == {"", 0, False}
        assert "Discord is off" in render_config(state, cfg)


class TestNonInteractive:
    def test_no_tty_keeps_the_template_path(self, tmp_path):
        """CliRunner has no tty, so init must not try to prompt."""
        from openstore.cli import app
        from typer.testing import CliRunner

        res = CliRunner().invoke(app, ["init", "--merchant", "Shop", "--output", str(tmp_path)])
        assert res.exit_code == 0, res.output
        data = yaml.safe_load((tmp_path / "shop.yaml").read_text())
        assert data["razorpay"]["key_id"] == "${RAZORPAY_KEY_ID}"

    def test_missing_merchant_without_tty_fails_loud(self, tmp_path):
        from openstore.cli import app
        from typer.testing import CliRunner

        res = CliRunner().invoke(app, ["init", "--output", str(tmp_path)])
        assert res.exit_code == 2
        assert "--merchant is required" in res.output
