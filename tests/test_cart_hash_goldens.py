"""The frozen preimage, compared byte-for-byte forever.

If a change to `core/canonical.py` makes these fail, the change is wrong — not
the vectors. They are what every translator's Transcript is pinned against
(SPEC §9), and regenerating them to make a test pass is the one move that makes
the golden replays worthless.
"""

from __future__ import annotations

import json
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import pytest
from openstore.sidecar.core.canonical import canonical_bytes, cart_hash, pii_commit, quote_hash

VECTORS = json.loads(Path("tests/GOLDEN/cart_hash/vectors.json").read_text(encoding="utf-8"))[
    "vectors"
]


def _ids() -> list[str]:
    return [v["name"] for v in VECTORS]


def test_three_vectors_exist() -> None:
    assert _ids() == ["minimal", "mixed-basket", "with-discount"]


@pytest.mark.parametrize("vector", VECTORS, ids=_ids())
def test_hashes_reproduce(vector: dict[str, Any]) -> None:
    inp, expected = vector["inputs"], vector["expected"]
    salt = bytes.fromhex(inp["order_salt_hex"])

    qh = quote_hash(inp["quote"])
    dh = pii_commit(salt, inp["destination"])
    ch = pii_commit(salt, inp["contact"])

    assert qh == expected["quote_hash"]
    assert dh == expected["destination_hash"]
    assert ch == expected["contact_hash"]
    assert (
        cart_hash(
            lines=inp["lines"],
            quote_hash_hex=qh,
            destination_hash=dh,
            contact_hash=ch,
            fulfillment_option_id=inp["fulfillment_option_id"],
            total_minor=inp["quote"]["total_minor"],
            currency=inp["quote"]["currency"],
            merchant_domain=inp["merchant_domain"],
            expiry_utc=inp["expiry_utc"],
        )
        == expected["cart_hash"]
    )


@pytest.mark.parametrize("vector", VECTORS, ids=_ids())
def test_line_order_does_not_change_the_hash(vector: dict[str, Any]) -> None:
    """An agent that sends its lines in a different order gets the same binding.
    Without this the Consumer's tap would depend on basket insertion order."""
    inp = vector["inputs"]
    common = {
        "quote_hash_hex": vector["expected"]["quote_hash"],
        "destination_hash": vector["expected"]["destination_hash"],
        "contact_hash": vector["expected"]["contact_hash"],
        "fulfillment_option_id": inp["fulfillment_option_id"],
        "total_minor": inp["quote"]["total_minor"],
        "currency": inp["quote"]["currency"],
        "merchant_domain": inp["merchant_domain"],
        "expiry_utc": inp["expiry_utc"],
    }
    assert (
        cart_hash(lines=list(reversed(inp["lines"])), **common) == vector["expected"]["cart_hash"]
    )


@pytest.mark.parametrize("vector", VECTORS, ids=_ids())
def test_quote_line_order_does_not_change_the_quote_hash(vector: dict[str, Any]) -> None:
    """Likewise for a Merchant that emits its tax lines in a different order."""
    quote = dict(vector["inputs"]["quote"])
    quote["tax_lines"] = list(reversed(quote["tax_lines"]))
    quote["lines"] = list(reversed(quote["lines"]))
    assert quote_hash(quote) == vector["expected"]["quote_hash"]


@pytest.mark.parametrize(
    "field",
    ["fulfillment_option_id", "total_minor", "currency", "merchant_domain", "expiry_utc"],
)
def test_every_preimage_field_moves_the_hash(field: str) -> None:
    """Each field is in the preimage because it changes the binding. A field that
    can be edited without moving `cart_hash` is one the Consumer did not authorize."""
    inp = VECTORS[1]["inputs"]
    base = {
        "lines": inp["lines"],
        "quote_hash_hex": VECTORS[1]["expected"]["quote_hash"],
        "destination_hash": VECTORS[1]["expected"]["destination_hash"],
        "contact_hash": VECTORS[1]["expected"]["contact_hash"],
        "fulfillment_option_id": inp["fulfillment_option_id"],
        "total_minor": inp["quote"]["total_minor"],
        "currency": inp["quote"]["currency"],
        "merchant_domain": inp["merchant_domain"],
        "expiry_utc": inp["expiry_utc"],
    }
    tampered = dict(base)
    tampered[field] = 1 if field == "total_minor" else "tampered"
    assert cart_hash(**tampered) != cart_hash(**base)


def test_destination_edit_moves_the_commitment() -> None:
    """A Destination change that leaves the total alone must still be visible:
    otherwise an edit after render redirects a parcel somebody already paid for."""
    inp = VECTORS[1]["inputs"]
    salt = bytes.fromhex(inp["order_salt_hex"])
    moved = dict(inp["destination"], line1="Somewhere else entirely")
    assert pii_commit(salt, moved) != VECTORS[1]["expected"]["destination_hash"]


def test_a_different_salt_gives_a_different_commitment() -> None:
    """Erasure takes the salt with the order row, and the commitment can never be
    opened again (ADR-0011). That only holds if the salt is load-bearing."""
    inp = VECTORS[0]["inputs"]
    other = bytes.fromhex("ffeeddccbbaa99887766554433221100")
    assert pii_commit(other, inp["destination"]) != VECTORS[0]["expected"]["destination_hash"]


def test_salt_must_be_128_bits() -> None:
    with pytest.raises(ValueError, match="128 bits"):
        pii_commit(b"short", {"a": 1})


def test_canonical_bytes_are_key_order_independent() -> None:
    assert canonical_bytes({"b": 1, "a": 2}) == canonical_bytes({"a": 2, "b": 1})


def test_canonical_bytes_refuse_non_finite() -> None:
    """Fail loud: a NaN in a money path must not serialize to `NaN` and hash."""
    with pytest.raises(ValueError):
        canonical_bytes({"amount": float("nan")})


def test_canonical_bytes_are_utf8_not_escaped() -> None:
    """A non-ASCII city hashes as its own bytes, not as an escape sequence."""
    assert canonical_bytes({"city": "बेंगलुरु"}) == '{"city":"बेंगलुरु"}'.encode()


def _extract_tax(inclusive_minor: int, rate_bp: int) -> int:
    value = Decimal(inclusive_minor) * Decimal(rate_bp) / Decimal(10000 + rate_bp)
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _apportion(amount: int, weights: list[int]) -> list[int]:
    """§16.11 step 4 largest-remainder, as a reference for the vectors only.
    The shipping implementation is B2's (door 9) and the check is A3's."""
    total = sum(weights)
    raw = [Decimal(amount) * Decimal(w) / Decimal(total) for w in weights]
    shares = [int(r) for r in raw]
    leftover = amount - sum(shares)
    order = sorted(range(len(raw)), key=lambda i: (-(raw[i] - shares[i]), i))
    for i in order[:leftover]:
        shares[i] += 1
    assert sum(shares) == amount
    return shares


def test_mixed_basket_reproduces_the_pinned_worked_example() -> None:
    """SPECS/PLAN.md §16.11's table, recomputed. If this disagrees with the
    table, the implementation is wrong — not the table."""
    quote = VECTORS[1]["inputs"]["quote"]
    weights = [ln["line_total_minor"] for ln in quote["lines"]]
    assert weights == [99800, 150000]

    shipping = _apportion(quote["fulfillment_chosen"]["cost_minor"], weights)
    assert shipping == [3955, 5945]  # the leftover paise goes to the larger fraction

    igst = _extract_tax(99800 + shipping[0], 1800)
    assert igst == 15827

    seat_tax = _extract_tax(150000 + shipping[1], 1800)
    assert seat_tax == 23788
    assert (seat_tax // 2, seat_tax - seat_tax // 2) == (11894, 11894)

    assert {t["amount_minor"] for t in quote["tax_lines"]} == {15827, 11894}
    assert quote["total_minor"] == 259700


def test_odd_paise_goes_to_sgst() -> None:
    """§16.11 step 6, the trap the whole section exists for. `minimal` extracts
    3783 paise of tax, which does not halve evenly."""
    quote = VECTORS[0]["inputs"]["quote"]
    tax = _extract_tax(19900 + 4900, 1800)
    assert tax == 3783
    cgst = next(t["amount_minor"] for t in quote["tax_lines"] if t["kind"] == "CGST")
    sgst = next(t["amount_minor"] for t in quote["tax_lines"] if t["kind"] == "SGST")
    assert (cgst, sgst) == (tax // 2, tax - tax // 2) == (1891, 1892)
    assert sgst > cgst


def test_discount_and_shipping_both_apportion_by_largest_remainder() -> None:
    weights = [89900, 44900]
    assert _apportion(10000, weights) == [6669, 3331]
    assert _apportion(4900, weights) == [3268, 1632]
    assert _extract_tax(89900 - 6669 + 3268, 1800) == 13195
    assert _extract_tax(44900 - 3331 + 1632, 300) == 1258
    assert VECTORS[2]["inputs"]["quote"]["total_minor"] == 129700


@pytest.mark.parametrize("vector", VECTORS, ids=_ids())
def test_every_amount_is_an_integer(vector: dict[str, Any]) -> None:
    """Money is paise, and a float in a money path is a bug that rounds silently."""

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key.endswith(("_minor", "_bp")) or key == "qty":
                    assert isinstance(value, int), f"{key} is {type(value).__name__}"
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(vector["inputs"]["quote"])
