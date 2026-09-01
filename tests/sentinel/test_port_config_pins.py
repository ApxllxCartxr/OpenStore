# tests/sentinel/test_port_config_pins.py
# S10.3 — Port/config pins: forgeable or drifting operational constants fail the
# build. Two-merchant demo couples gelateria (default :8000) and chai (:8001).

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SERVER_PORT_DEFAULT = 8000
CHAI_PORT = 8001


def test_serve_defaults_are_pinned():
    """Default demo port stays 8000; any drift breaks the two-merchant demo."""
    cli_src = (ROOT / "src/openstore/cli.py").read_text()
    m = re.search(r'port: int = typer\.Option\((\d+),\s*"--port"', cli_src)
    assert m, "serve --port default not found in cli.py"
    assert int(m.group(1)) == SERVER_PORT_DEFAULT


def test_chai_config_pins_chai_to_8001():
    chai = yaml.safe_load((ROOT / "chai.yaml").read_text())
    assert chai["webauthn"]["origin"].endswith(f":{CHAI_PORT}")
    assert "chai" in chai["merchant"]["name"].lower()


def test_chai_and_gelateria_are_distinct_merchants():
    c = yaml.safe_load((ROOT / "chai.yaml").read_text())["merchant"]
    g = yaml.safe_load((ROOT / "gelateria.yaml").read_text())["merchant"]
    assert c["name"] != g["name"]
    assert c["currency"] == "INR" and g["currency"] == "INR"


def test_merchant_currency_default_is_inr():
    import openstore.config as config_mod
    assert config_mod.MerchantConfig.model_fields["currency"].default == "INR"


def test_money_config_defaults_are_integer():
    """No float money field anywhere in config/models (floats forbidden R3)."""
    import openstore.config as config_mod
    money_fields = [
        name for name in (
            "max_spend_per_tx_minor", "max_spend_total_minor", "min_bps", "max_bps",
        ) if name in config_mod.Settings.model_fields or any(
            name in cls.model_fields for cls in (
                config_mod.CampaignSettings, config_mod.MerchantConfig,
            )
        )
    ]
    for name in money_fields:
        owner = next(
            cls for cls in (config_mod.CampaignSettings, config_mod.MerchantConfig)
            if name in cls.model_fields
        )
        assert owner.model_fields[name].annotation is int, f"{name} is not int"
