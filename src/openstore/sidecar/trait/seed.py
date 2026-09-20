"""The §16.3 seed catalogue, as data.

Twelve Product Groups expanding to **15** Catalogue Items. The table in §16.3
has sixteen rows and one of them — `SD-TOTE-BLK-L` — is marked DO NOT SEED: the
combination was never made, and it exists in the table only so nobody helpfully
fills the gap. Seeding it breaks two gates at once ("zero null-stock rows" and
"the unavailable combination renders as unavailable"), so it is absent here and
a test asserts its absence.

Three distinct GST rates (3%, 12%, 18%) are deliberate: they make the
apportionment and largest-remainder rounding actually exercise.
"""

from __future__ import annotations

from openstore.sidecar.trait.fake import (
    FakeDiscount,
    FakeGroup,
    FakeItem,
    FakeMerchant,
    FakeZone,
)

#: (sku, group, name, price_minor, hsn_sac, gst_rate_bp, stock, low_stock, options, tags)
SEED_ITEMS: list[tuple[str, str, str, int, str, int, int, int, dict[str, str], list[str]]] = [
    ("SD-TOTE-BLK-M", "tote", "Tote — black / M", 89900, "4202", 1800, 12, 3,
     {"colour": "black", "size": "M"}, []),
    # SD-TOTE-BLK-L is DELIBERATELY ABSENT — black/L was never made (§16.3).
    ("SD-TOTE-RED-M", "tote", "Tote — red / M", 89900, "4202", 1800, 7, 3,
     {"colour": "red", "size": "M"}, []),
    ("SD-TOTE-RED-L", "tote", "Tote — red / L", 99900, "4202", 1800, 4, 3,
     {"colour": "red", "size": "L"}, []),
    ("SD-CAP-S", "cap", "Cap — S", 64900, "6505", 1200, 9, 3, {"size": "S"}, []),
    ("SD-CAP-M", "cap", "Cap — M", 64900, "6505", 1200, 2, 3, {"size": "M"}, []),
    ("SD-STICKERS", "stickers", "Sticker pack", 19900, "4911", 1800, 40, 5, {}, []),
    ("SD-KEYCHAIN", "keychain", "Keychain", 29900, "8308", 1800, 25, 5, {}, []),
    ("SD-HAIRCLIPS", "hairclips", "Hair clips", 34900, "9615", 1800, 18, 3, {}, []),
    ("SD-PHONECHARM", "phonecharm", "Phone charm", 44900, "7117", 300, 15, 3, {}, []),
    ("SD-PINSET", "pinset", "Pin set", 39900, "7117", 300, 11, 3, {}, []),
    ("SD-PLUSH-MINI", "plush", "Plush mini", 129900, "9503", 1200, 10, 2, {}, ["limited"]),
    ("SD-CHARMBAR-SEAT", "charmbar", "Charm-bar seat", 150000, "999799", 1800, 10, 2, {},
     ["service"]),
    ("SD-GIFTWRAP", "giftwrap", "Gift-wrap", 9900, "", 0, 100, 10, {}, ["addon"]),
    ("SD-EXTRACHARM", "extracharm", "Extra charm", 14900, "", 0, 60, 10, {}, ["addon"]),
    ("SD-RECALLED", "recalled", "Recalled item", 49900, "4202", 1800, 5, 3, {}, ["recalled"]),
]  # fmt: skip

SEED_GROUPS: list[tuple[str, str, dict[str, list[str]]]] = [
    ("tote", "Tote", {"colour": ["black", "red"], "size": ["M", "L"]}),
    ("cap", "Cap", {"size": ["S", "M"]}),
    ("stickers", "Sticker pack", {}),
    ("keychain", "Keychain", {}),
    ("hairclips", "Hair clips", {}),
    ("phonecharm", "Phone charm", {}),
    ("pinset", "Pin set", {}),
    ("plush", "Plush mini", {}),
    ("charmbar", "Charm-bar seat", {}),
    ("giftwrap", "Gift-wrap", {}),
    ("extracharm", "Extra charm", {}),
    ("recalled", "Recalled item", {}),
]


def seeded(**overrides: object) -> FakeMerchant:
    """A SpoiledDuckie the conformance suite can reason about, per §16.3–§16.6."""
    merchant = FakeMerchant(
        zones=[
            # §16.5. Costs are inclusive of GST, like every other seeded price.
            FakeZone("karnataka", "Karnataka", 4900, 2, states=("KA",)),
            FakeZone("rest-of-india", "Rest of India", 9900, 5, states=None),
        ],
        discounts={
            "SPOILED10": FakeDiscount("SPOILED10", -10000, "Launch code", max_uses=50),
            # High-entropy on purpose: B3 refuses guessable private codes at
            # creation, and seeding is not a licence to seed TEST1.
            "DUCK-7F3K-9QWX": FakeDiscount("DUCK-7F3K-9QWX", -25000, "Private code", max_uses=1),
        },
    )
    for gid, name, axes in SEED_GROUPS:
        merchant.groups[gid] = FakeGroup(id=gid, slug=gid, name=name, option_axes=axes)

    for sku, gid, name, price, hsn, rate, stock, low, options, tags in SEED_ITEMS:
        merchant.items[sku] = FakeItem(
            sku=sku,
            group_id=gid,
            name=name,
            price_minor=price,
            hsn_sac=hsn,
            gst_rate_bp=rate,
            low_stock_threshold=low,
            options=options,
            tags=tags,
        )
        merchant.stock[sku] = stock

    for key, value in overrides.items():
        setattr(merchant, key, value)
    return merchant


def flat_price() -> FakeMerchant:
    """A Merchant with no tax and free delivery.

    The conformance gate requires a flat-price Merchant's zero-value tax and
    fulfillment lines to pass **unchanged**: the lines exist and carry 0 rather
    than being omitted. A consumer of the Quote that has to handle "the key is
    missing" as well as "the value is zero" has two paths where it needs one.
    """
    merchant = seeded()
    merchant.tax_inclusive = False
    merchant.zones = [FakeZone("free", "Free delivery", 0, 7, states=None)]
    for item in merchant.items.values():
        item.gst_rate_bp = 0
    return merchant
