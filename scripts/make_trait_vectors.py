"""Generate the vectors a second trait implementation is checked against.

Two files, for the two things a Merchant-side implementation has to get exactly
right before anything else works at all:

- `pricing-vectors.json` — §16.11's arithmetic, to the paise.
- `signing-vectors.json` — the HMAC preimage, byte for byte.

**Why a vector file rather than a shared library.** §16.11 now has four
independent implementations: the sidecar's Gate check, the conformance fake,
the demo storefront's TypeScript, and the WooCommerce plugin's PHP. They share
no code on purpose — if they did, `quote-consistent` would prove only that the
sidecar agrees with itself. What they need instead is a way to disagree loudly,
and these are it: inputs and the exact paise each one must produce.

The cases are generated rather than hand-written, and deliberately chosen to sit
on the arithmetic's edges — odd tax that forces SGST to take the spare paise,
three-way apportionment that does not divide, a discount and a shipping cost
apportioned on the same basis, a service line whose Place of Supply differs from
the rest of the basket.

`python scripts/make_pricing_vectors.py --check` fails if the file no longer
matches what Python produces; `integrations/woocommerce/tests/run-vectors.php`
fails if PHP disagrees with the file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from openstore.sidecar.trait.fake import FakeDiscount, FakeItem, FakeMerchant, FakeZone

VECTORS = Path("integrations/woocommerce/tests/pricing-vectors.json")
SIGNING_VECTORS = Path("integrations/woocommerce/tests/signing-vectors.json")

#: Signature cases. The preimage is `path \n timestamp \n nonce \n body`, and
#: each of these moves exactly one of those four — because an implementation
#: that concatenated them in a different order, or left the path out, produces a
#: signature that is stable, plausible and wrong on every request.
SIGNING_CASES: list[dict[str, Any]] = [
    {
        "name": "an empty body still signs the path, timestamp and nonce",
        "secret": "conformance-secret",
        "path": "/trait/catalog.read",
        "timestamp": 1758400000,
        "nonce": "0123456789abcdef0123456789abcdef",
        "body": "",
    },
    {
        "name": "the ordinary case",
        "secret": "conformance-secret",
        "path": "/trait/quote",
        "timestamp": 1758400000,
        "nonce": "0123456789abcdef0123456789abcdef",
        "body": '{"lines":[{"qty":1,"sku":"SD-TOTE-BLK-M"}]}',
    },
    {
        "name": "the path is covered: release and commit carry the same body",
        "secret": "conformance-secret",
        "path": "/trait/release",
        "timestamp": 1758400000,
        "nonce": "0123456789abcdef0123456789abcdef",
        "body": '{"order_id":"ord_1"}',
    },
    {
        "name": "the same body at a different door signs differently",
        "secret": "conformance-secret",
        "path": "/trait/commit",
        "timestamp": 1758400000,
        "nonce": "0123456789abcdef0123456789abcdef",
        "body": '{"order_id":"ord_1"}',
    },
    {
        "name": "whitespace in the body is part of the signature",
        "secret": "conformance-secret",
        "path": "/trait/commit",
        "timestamp": 1758400000,
        "nonce": "0123456789abcdef0123456789abcdef",
        "body": '{"order_id": "ord_1"}',
    },
    {
        "name": "a non-ascii body signs over its utf-8 bytes",
        "secret": "conformance-secret",
        "path": "/trait/orders.create",
        "timestamp": 1758400000,
        "nonce": "0123456789abcdef0123456789abcdef",
        "body": '{"city":"Bengalūru"}',
    },
]

#: Every case is `(name, items, lines, zone, discount, destination_state,
#: home_state, tax_inclusive)`. Items carry only what pricing reads.
CASES: list[dict[str, Any]] = [
    {
        "name": "one line, no shipping, no discount",
        "items": [
            {"sku": "A", "price_minor": 100000, "gst_rate_bp": 1800, "hsn_sac": "4202", "tags": []}
        ],
        "lines": [{"sku": "A", "qty": 1}],
        "zone": {"id": "flat", "label": "Flat", "cost_minor": 0, "eta_days": 3},
        "discount": None,
        "destination_state": "MH",
        "home_state": "KA",
        "tax_inclusive": True,
    },
    {
        "name": "intra-state, odd tax: SGST takes the spare paise",
        "items": [
            {"sku": "A", "price_minor": 99901, "gst_rate_bp": 1800, "hsn_sac": "4202", "tags": []}
        ],
        "lines": [{"sku": "A", "qty": 1}],
        "zone": {"id": "flat", "label": "Flat", "cost_minor": 4900, "eta_days": 2},
        "discount": None,
        "destination_state": "KA",
        "home_state": "KA",
        "tax_inclusive": True,
    },
    {
        "name": "three lines, shipping that does not divide by three",
        "items": [
            {"sku": "A", "price_minor": 33333, "gst_rate_bp": 1800, "hsn_sac": "4202", "tags": []},
            {"sku": "B", "price_minor": 33333, "gst_rate_bp": 1800, "hsn_sac": "4202", "tags": []},
            {"sku": "C", "price_minor": 33334, "gst_rate_bp": 1800, "hsn_sac": "4202", "tags": []},
        ],
        "lines": [{"sku": "A", "qty": 1}, {"sku": "B", "qty": 1}, {"sku": "C", "qty": 1}],
        "zone": {"id": "flat", "label": "Flat", "cost_minor": 10000, "eta_days": 5},
        "discount": None,
        "destination_state": "MH",
        "home_state": "KA",
        "tax_inclusive": True,
    },
    {
        "name": "discount and shipping apportioned on the same basis",
        "items": [
            {"sku": "A", "price_minor": 89900, "gst_rate_bp": 1800, "hsn_sac": "4202", "tags": []},
            {"sku": "B", "price_minor": 150000, "gst_rate_bp": 1200, "hsn_sac": "7117", "tags": []},
        ],
        "lines": [{"sku": "A", "qty": 1}, {"sku": "B", "qty": 1}],
        "zone": {"id": "flat", "label": "Flat", "cost_minor": 9900, "eta_days": 5},
        "discount": {"code": "SAVE", "label": "Save", "amount_minor": -12345},
        "destination_state": "MH",
        "home_state": "KA",
        "tax_inclusive": True,
    },
    {
        "name": "a service line keeps its Place of Supply at home",
        "items": [
            {"sku": "A", "price_minor": 89900, "gst_rate_bp": 1800, "hsn_sac": "4202", "tags": []},
            {
                "sku": "S",
                "price_minor": 50000,
                "gst_rate_bp": 1800,
                "hsn_sac": "9983",
                "tags": ["service"],
            },
        ],
        "lines": [{"sku": "A", "qty": 1}, {"sku": "S", "qty": 1}],
        "zone": {"id": "flat", "label": "Flat", "cost_minor": 9900, "eta_days": 5},
        "discount": None,
        "destination_state": "MH",
        "home_state": "KA",
        "tax_inclusive": True,
    },
    {
        "name": "an Add-on folds into its parent and never quotes itself",
        "items": [
            {"sku": "A", "price_minor": 89900, "gst_rate_bp": 1800, "hsn_sac": "4202", "tags": []},
            {
                "sku": "W",
                "price_minor": 9900,
                "gst_rate_bp": 1800,
                "hsn_sac": "4202",
                "tags": ["addon"],
            },
        ],
        "lines": [{"sku": "A", "qty": 1}, {"sku": "W", "qty": 1, "parent": "A"}],
        "zone": {"id": "flat", "label": "Flat", "cost_minor": 9900, "eta_days": 5},
        "discount": None,
        "destination_state": "MH",
        "home_state": "KA",
        "tax_inclusive": True,
    },
    {
        "name": "quantities above one, two rates in one basket",
        "items": [
            {"sku": "A", "price_minor": 12345, "gst_rate_bp": 500, "hsn_sac": "4202", "tags": []},
            {"sku": "B", "price_minor": 67891, "gst_rate_bp": 2800, "hsn_sac": "8517", "tags": []},
        ],
        "lines": [{"sku": "A", "qty": 3}, {"sku": "B", "qty": 2}],
        "zone": {"id": "flat", "label": "Flat", "cost_minor": 7777, "eta_days": 4},
        "discount": {"code": "TEN", "label": "Ten", "amount_minor": -1000},
        "destination_state": "KA",
        "home_state": "KA",
        "tax_inclusive": True,
    },
    {
        "name": "a zero-rated line beside a taxed one",
        "items": [
            {"sku": "A", "price_minor": 50000, "gst_rate_bp": 0, "hsn_sac": "0401", "tags": []},
            {"sku": "B", "price_minor": 50000, "gst_rate_bp": 1800, "hsn_sac": "4202", "tags": []},
        ],
        "lines": [{"sku": "A", "qty": 1}, {"sku": "B", "qty": 1}],
        "zone": {"id": "flat", "label": "Flat", "cost_minor": 5000, "eta_days": 5},
        "discount": None,
        "destination_state": "MH",
        "home_state": "KA",
        "tax_inclusive": True,
    },
    {
        "name": "tax added rather than included",
        "items": [
            {"sku": "A", "price_minor": 100000, "gst_rate_bp": 1800, "hsn_sac": "4202", "tags": []}
        ],
        "lines": [{"sku": "A", "qty": 1}],
        "zone": {"id": "flat", "label": "Flat", "cost_minor": 0, "eta_days": 3},
        "discount": None,
        "destination_state": "MH",
        "home_state": "KA",
        "tax_inclusive": False,
    },
]


def price(case: dict[str, Any]) -> dict[str, Any]:
    """Price one case with the Python implementation, through the fake itself.

    Through `FakeMerchant` rather than a helper written for this script: a
    reimplementation here would be a fifth implementation, and agreeing with it
    would prove nothing about the one the conformance suite actually runs.
    """
    merchant = FakeMerchant(
        home_state=case["home_state"],
        tax_inclusive=case["tax_inclusive"],
        unserviceable_prefix="ZZZZZZ",
    )
    for item in case["items"]:
        merchant.items[item["sku"]] = FakeItem(
            sku=item["sku"],
            group_id=f"grp_{item['sku']}",
            name=item["sku"],
            price_minor=item["price_minor"],
            hsn_sac=item["hsn_sac"],
            gst_rate_bp=item["gst_rate_bp"],
            tags=list(item["tags"]),
        )
        merchant.stock[item["sku"]] = 100
    zone = case["zone"]
    merchant.zones = [
        FakeZone(
            id=zone["id"],
            label=zone["label"],
            cost_minor=zone["cost_minor"],
            eta_days=zone["eta_days"],
        )
    ]
    if case["discount"]:
        merchant.discounts[case["discount"]["code"]] = FakeDiscount(
            code=case["discount"]["code"],
            amount_minor=case["discount"]["amount_minor"],
            label=case["discount"]["label"],
            max_uses=99,
        )

    payload: dict[str, Any] = {
        "lines": case["lines"],
        "destination": {
            "line1": "1",
            "city": "Somewhere",
            "state": case["destination_state"],
            "postal_code": "560038",
            "country": "IN",
        },
    }
    if case["discount"]:
        payload["discount_code"] = case["discount"]["code"]
    return merchant.quote(payload)


def build_signing() -> dict[str, Any]:
    """The expected signature for each case, from the sidecar's own `sign`."""
    from openstore.sidecar.trait.signing import sign

    return {
        "note": (
            "HMAC preimage vectors: path, timestamp, nonce and body joined by "
            "newlines. Generated by scripts/make_trait_vectors.py."
        ),
        "cases": [
            {
                **case,
                "expected": sign(
                    case["secret"],
                    body=case["body"].encode("utf-8"),
                    timestamp=case["timestamp"],
                    nonce=case["nonce"],
                    path=case["path"],
                ),
            }
            for case in SIGNING_CASES
        ],
    }


def build() -> dict[str, Any]:
    return {
        "note": (
            "§16.11 agreement vectors. Inputs, and the exact paise every "
            "implementation must produce. Generated by scripts/make_pricing_vectors.py."
        ),
        "cases": [{**case, "expected": price(case)} for case in CASES],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="make-trait-vectors", description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the file on disk is not what Python produces now",
    )
    args = parser.parse_args(argv)

    wanted = {
        VECTORS: json.dumps(build(), indent=2, sort_keys=True) + "\n",
        SIGNING_VECTORS: json.dumps(build_signing(), indent=2, sort_keys=True) + "\n",
    }

    if args.check:
        for path, built in wanted.items():
            if not path.exists():
                print(f"{path} does not exist; run without --check to write it", file=sys.stderr)
                return 1
            if path.read_text(encoding="utf-8") != built:
                print(
                    f"{path} no longer matches the Python implementation. Either the "
                    f"contract changed (regenerate, and make every other implementation "
                    f"agree) or it drifted (fix it).",
                    file=sys.stderr,
                )
                return 1
            print(f"{path}: agrees with Python")
        return 0

    for path, built in wanted.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(built, encoding="utf-8")
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
