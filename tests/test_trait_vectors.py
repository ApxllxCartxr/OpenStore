"""The vectors a second Merchant-side implementation is checked against.

These exist because §16.11 now has four implementations and the HMAC preimage
has three, and none of them share a line of code. That is deliberate — a shared
library would make the Gate's `quote-consistent` check prove only that the
sidecar agrees with itself — but it means the only thing keeping them in step is
a set of inputs with the exact answer written down.

**This test guards the Python side of that.** It fails when the checked-in
vectors stop matching what the sidecar produces, which is either an arithmetic
change (regenerate, then make every other implementation agree) or a drift (fix
it). The PHP side is checked by `integrations/woocommerce/tests/run-*.php`,
which needs a PHP runtime and therefore runs in CI rather than here.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.make_trait_vectors import SIGNING_VECTORS, VECTORS, build, build_signing


def test_the_pricing_vectors_are_what_python_produces_now() -> None:
    on_disk = json.loads(Path(VECTORS).read_text(encoding="utf-8"))
    assert on_disk == build(), (
        "the checked-in pricing vectors no longer match §16.11 as this codebase "
        "implements it. Run `uv run scripts/make_trait_vectors.py` and make every "
        "other implementation agree."
    )


def test_the_signing_vectors_are_what_python_produces_now() -> None:
    on_disk = json.loads(Path(SIGNING_VECTORS).read_text(encoding="utf-8"))
    assert on_disk == build_signing()


def test_the_path_is_actually_in_the_preimage() -> None:
    """Two cases differ only by door. Identical signatures would mean the path
    contributes nothing — which makes a signed `release` a signed `commit`."""
    cases = {c["name"]: c for c in build_signing()["cases"]}
    release = cases["the path is covered: release and commit carry the same body"]
    commit = cases["the same body at a different door signs differently"]

    assert release["body"] == commit["body"]
    assert release["expected"] != commit["expected"]


def test_every_pricing_vector_totals_what_its_parts_say() -> None:
    """A vector file is only as good as its arithmetic. This re-checks §16.11's
    step 8 against every expected Quote, so a bad vector cannot quietly become
    the thing four implementations are held to."""
    for case in build()["cases"]:
        quote = case["expected"]
        subtotal = sum(line["line_total_minor"] for line in quote["lines"])
        discounts = sum(line["amount_minor"] for line in quote["discount_lines"])
        shipping = quote["fulfillment_chosen"]["cost_minor"]

        assert quote["subtotal_minor"] == subtotal, case["name"]
        assert quote["total_minor"] == subtotal + shipping + discounts, case["name"]
        assert quote["round_off_minor"] == 0, case["name"]
