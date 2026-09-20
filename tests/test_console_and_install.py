"""The console, the feed, the logs, and the install gate."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openstore.sidecar.app import app
from openstore.sidecar.console.render import TABS
from openstore.sidecar.console.routes import (
    PUBLIC_AGENTIC_PATHS,
    get_console_store,
    record_overdue_hold,
    set_policy,
)
from openstore.sidecar.core.codes import AvailabilityBucket, PaymentMethod
from openstore.sidecar.core.feed import availability_for, build_feed, feed_price
from openstore.sidecar.core.logging import REDACTED_KEYS, money_event, redact
from openstore.sidecar.core.settings import get_settings
from openstore.sidecar.gate.policy import Policy
from openstore.sidecar.trait.models import CatalogueItem, ProductGroup
from openstore.sidecar.trait.seed import SEED_ITEMS, seeded

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _fresh_console(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.chdir(REPO)
    monkeypatch.setenv("OPENSTORE_MERCHANT_DOMAIN", "spoiledduckie.localhost")
    get_settings.cache_clear()
    get_console_store.cache_clear()
    yield
    get_settings.cache_clear()
    get_console_store.cache_clear()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


# ── Every shipped tab renders ────────────────────────────────────────────────


@pytest.mark.parametrize("tab", [name for name, _ in TABS])
def test_every_shipped_tab_renders_against_seeded_data(client: TestClient, tab: str) -> None:
    response = client.get(f"/agentic/{tab}")
    assert response.status_code == 200
    assert "spoiledduckie.localhost" in response.text
    assert 'aria-current="page"' in response.text


def test_the_cut_tabs_are_absent_rather_than_empty(client: TestClient) -> None:
    """Attribution and the full health panel were cut to fund A6's four
    protocols. A tab that renders an empty panel would look broken; an absent
    one is a decision."""
    names = {name for name, _ in TABS}
    assert "attribution" not in names
    assert client.get("/agentic/attribution").status_code == 404


def test_the_console_is_mono_dominant_with_no_animation(client: TestClient) -> None:
    """§4: it should read like something you check at 2am."""
    body = client.get("/agentic/health").text
    assert "Iosevka Term SS08" in body
    assert "tabular-nums" in body
    assert "prefers-reduced-motion" in body
    assert "@keyframes" not in body


def test_no_component_references_a_literal_colour(client: TestClient) -> None:
    """Every value is a token, so the console inherits the palette and the dark
    theme without knowing either."""
    import re

    from openstore.sidecar.console.render import CONSOLE_CSS

    declarations = re.findall(r":\s*([^;{}]+);", CONSOLE_CSS)
    for value in declarations:
        if "#" in value:
            assert "var(--" in value, f"literal colour outside a token fallback: {value.strip()}"


# ── Policy edits reach the Gate ──────────────────────────────────────────────


def test_a_policy_edit_changes_gate_outcomes_without_a_restart(client: TestClient) -> None:
    before = client.get("/agentic/policy").text
    assert "₹25,000.00" in before

    set_policy(Policy(per_order_cap_minor=100_000, window_open=False))

    after = client.get("/agentic/policy").text
    assert "₹1,000.00" in after
    assert "closed" in after
    # And the Gate reads the same object, not a copy.
    assert get_console_store().policy.per_order_cap_minor == 100_000


def test_the_policy_tab_states_that_caps_are_evaluated_at_the_group(
    client: TestClient,
) -> None:
    """Stated in the UI so nobody wonders why three different totes counted as
    one item."""
    body = client.get("/agentic/policy").text
    assert "Product Group" in body
    assert "two of each colour" in body


# ── Health: the two things that could not be dropped ─────────────────────────


def test_an_overdue_hold_appears_and_names_the_release_path(client: TestClient) -> None:
    """A wedged sidecar holds stock forever and the only defence is that
    somebody can see it."""
    assert "Nothing overdue" in client.get("/agentic/health").text

    record_overdue_hold(
        "ord_stuck",
        "confirmed",
        datetime.now(UTC) - timedelta(minutes=30),
        amount_minor=259700,
    )

    body = client.get("/agentic/health").text
    assert "ord_stuck" in body
    assert "₹2,597.00" in body
    assert "never a Merchant-side write" in body
    # And it is loud enough to see from any tab.
    assert "past their deadline" in client.get("/agentic/keys").text


def test_the_dev_allowlist_banner_shows_when_it_is_set(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert "Dev profile allowlist" not in client.get("/agentic/health").text

    monkeypatch.setenv("OPENSTORE_DEV_PROFILE_HOSTS", "buyer-chat:3001")
    get_settings.cache_clear()

    body = client.get("/agentic/health").text
    assert "Dev profile allowlist active" in body
    assert "buyer-chat:3001" in body
    assert "refuses to boot" in body


def test_first_run_refuses_until_the_export_is_acknowledged(client: TestClient) -> None:
    get_console_store().export_acknowledged = False
    body = client.get("/agentic/keys").text
    assert "Save your key export" in body
    assert "only way back" in body


# ── The auth boundary ────────────────────────────────────────────────────────


def test_approve_and_the_stylesheet_are_public_inside_an_authenticated_prefix() -> None:
    """The kind of thing a proxy config gets wrong once and then serves wrong
    forever, so it is asserted here rather than left to the Caddyfile."""
    assert "/agentic/approve" in PUBLIC_AGENTIC_PATHS
    assert "/agentic/static/tokens.css" in PUBLIC_AGENTIC_PATHS
    assert "/agentic/keys" not in PUBLIC_AGENTIC_PATHS


def test_the_caddyfile_routes_receipt_to_the_sidecar() -> None:
    """`/receipt/<id>` is public and served by the sidecar. If the proxy sent it
    to the store, no Consumer could open a receipt at all."""
    caddyfile = (REPO / "Caddyfile").read_text(encoding="utf-8")
    assert "/receipt/*" in caddyfile
    assert "chat.localhost" in caddyfile, "both site blocks, or the third surface 404s"


# ── The feed ─────────────────────────────────────────────────────────────────


def test_low_stock_folds_into_the_readers_enum() -> None:
    """`low-stock` is ours, not Merchant Center's. A feed carrying an invented
    value is rejected by the only reader that matters."""
    assert availability_for(AvailabilityBucket.IN_STOCK) == "in_stock"
    assert availability_for(AvailabilityBucket.LOW_STOCK) == "in_stock"
    assert availability_for(AvailabilityBucket.SOLD_OUT) == "out_of_stock"
    assert "low-stock" not in set(availability_for(b) for b in AvailabilityBucket)


def test_the_feed_never_carries_an_exact_count() -> None:
    """A feed is the easiest place in the system to leak inventory, because it
    is designed to be read by strangers."""
    merchant = seeded()
    groups = [
        ProductGroup(id=g.id, slug=g.slug, name=g.name, option_axes=g.option_axes)
        for g in merchant.groups.values()
    ]
    items = [
        CatalogueItem(
            sku=i.sku,
            group_id=i.group_id,
            options=i.options,
            name=i.name,
            price_minor=i.price_minor,
            tags=i.tags,
            low_stock_threshold=i.low_stock_threshold,
            hsn_sac=i.hsn_sac or "0000",
            gst_rate_bp=i.gst_rate_bp,
        )
        for i in merchant.items.values()
    ]
    feed = build_feed(groups, items, merchant.stock, base_url="https://spoiledduckie.localhost")

    rendered = str(feed)
    for sku, count in merchant.stock.items():
        assert f'"{count}"' not in rendered or count in (0,), f"{sku}'s count reached the feed"
    assert all(entry["availability"] in {"in_stock", "out_of_stock"} for entry in feed["items"])


def test_variants_are_grouped_by_item_group_id() -> None:
    """How variants are expressed in every feed that matters, so a Merchant can
    submit it by hand without a translation step."""
    merchant = seeded()
    groups = [ProductGroup(id=g.id, slug=g.slug, name=g.name) for g in merchant.groups.values()]
    items = [
        CatalogueItem(
            sku=i.sku,
            group_id=i.group_id,
            options=i.options,
            name=i.name,
            price_minor=i.price_minor,
            low_stock_threshold=i.low_stock_threshold,
            hsn_sac=i.hsn_sac or "0000",
            gst_rate_bp=i.gst_rate_bp,
        )
        for i in merchant.items.values()
    ]
    feed = build_feed(groups, items, merchant.stock, base_url="https://x")

    totes = [e for e in feed["items"] if e["item_group_id"] == "tote"]
    assert {e["id"] for e in totes} == {"SD-TOTE-BLK-M", "SD-TOTE-RED-M", "SD-TOTE-RED-L"}
    assert {e["color"] for e in totes} == {"black", "red"}


def test_an_unexposed_item_never_appears() -> None:
    merchant = seeded()
    groups = [ProductGroup(id=g.id, slug=g.slug, name=g.name) for g in merchant.groups.values()]
    items = [
        CatalogueItem(
            sku=i.sku,
            group_id=i.group_id,
            name=i.name,
            price_minor=i.price_minor,
            low_stock_threshold=i.low_stock_threshold,
            hsn_sac=i.hsn_sac or "0000",
            gst_rate_bp=i.gst_rate_bp,
        )
        for i in merchant.items.values()
    ]
    feed = build_feed(groups, items, merchant.stock, base_url="https://x", exposed={"SD-STICKERS"})
    assert [e["id"] for e in feed["items"]] == ["SD-STICKERS"]


def test_the_feed_price_is_tax_inclusive_and_reader_shaped() -> None:
    """India requires tax-inclusive prices in feeds; an Offer.price excluding
    GST understates every listing."""
    assert feed_price(89900) == "899.00 INR"
    assert feed_price(129900) == "1299.00 INR"


# ── Logs ─────────────────────────────────────────────────────────────────────


def test_a_money_line_carries_the_five_fields_and_no_pii() -> None:
    line = money_event(
        "gate.decide",
        order_id="ord_1",
        agent_id="agent_1",
        consumer_id="csm_abc",
        reason_code="sold-out",
        duration_ms=42,
        destination={"line1": "4th Cross", "city": "Bengaluru"},
        payer_handle="demo@okaxis",
    )
    assert line["order_id"] == "ord_1"
    assert line["reason_code"] == "sold-out"
    assert line["duration_ms"] == 42
    assert line["destination"] == "[redacted]"
    assert line["payer_handle"] == "[redacted]"
    assert "4th Cross" not in str(line)


def test_redaction_is_a_deny_list_by_name_not_a_guess() -> None:
    """A regex over values would miss `line1` and flag a SKU."""
    assert "order_salt" in REDACTED_KEYS
    assert "line1" in REDACTED_KEYS
    nested = redact({"order": {"contact": {"email": "a@b.c"}, "sku": "SD-TOTE-BLK-M"}})
    assert nested["order"]["contact"] == "[redacted]"
    assert nested["order"]["sku"] == "SD-TOTE-BLK-M"


# ── The install gate ─────────────────────────────────────────────────────────


def test_install_writes_everything_first_boot_needs(tmp_path: Path) -> None:
    """A Merchant who can point DNS can run it. Anything needing a hand-edited
    YAML before first boot is a bug in this gate."""
    from scripts.openstore_up import write_install

    written = write_install("shop.example", tmp_path)
    assert written["env"].exists()
    assert written["compose"].exists()
    assert written["caddyfile"].exists()

    env = written["env"].read_text()
    assert "OPENSTORE_MERCHANT_DOMAIN=shop.example" in env
    assert "WEBAUTHN_RP_ID=shop.example" in env
    assert "shop.example" in written["caddyfile"].read_text()
    # Every secret filled, none blank.
    for key in ("TRAIT_HMAC_SECRET", "DEPLOY_PSEUDONYM_KEY", "ADMIN_SEED_PASSWORD"):
        value = next(line.split("=", 1)[1] for line in env.splitlines() if line.startswith(key))
        assert len(value) > 20, f"{key} was not generated"


def test_the_dev_allowlist_is_empty_out_of_the_box(tmp_path: Path) -> None:
    from scripts.openstore_up import write_install

    env = write_install("shop.example", tmp_path)["env"].read_text()
    assert "OPENSTORE_DEV_PROFILE_HOSTS=\n" in env


def test_install_refuses_to_overwrite_a_live_env(tmp_path: Path) -> None:
    """Regenerating secrets over a live deployment orphans every signature it
    has made."""
    from scripts.openstore_up import write_install

    write_install("shop.example", tmp_path)
    with pytest.raises(SystemExit, match="Refusing to overwrite"):
        write_install("shop.example", tmp_path)


def test_the_generated_env_covers_every_template_variable(tmp_path: Path) -> None:
    """`.env.example` is the only place a variable is introduced, so the
    generator reads it rather than keeping a second list that would drift."""
    from scripts.openstore_up import write_install

    template = (REPO / ".env.example").read_text()
    declared = {
        line.split("=", 1)[0]
        for line in template.splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }
    generated = {
        line.split("=", 1)[0]
        for line in write_install("shop.example", tmp_path)["env"].read_text().splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }
    assert declared == generated


def test_the_env_file_is_not_world_readable(tmp_path: Path) -> None:
    from scripts.openstore_up import write_install

    written = write_install("shop.example", tmp_path)
    assert written["env"].stat().st_mode & 0o077 == 0


def test_seeded_policy_matches_the_pinned_values() -> None:
    """§16.4, so the demo is the documented configuration rather than whatever
    the defaults happened to be."""
    policy = Policy()
    assert policy.per_order_cap_minor == 2_500_000
    assert policy.per_order_line_count == 10
    assert policy.per_group_qty == 5
    assert policy.per_group_qty_overrides["plush"] == 2
    assert policy.blocked_tags == frozenset({"recalled"})
    assert policy.enabled_methods == frozenset({PaymentMethod.UPI, PaymentMethod.CASH_ON_DELIVERY})


def test_the_seed_carries_fifteen_items() -> None:
    assert len(SEED_ITEMS) == 15
