"""Typed shapes for the nine doors, mirroring `schema/doors.schema.json`.

The JSON Schema is the contract a Merchant implements against; these are what
the sidecar holds in memory. A test asserts the two agree, so a field added to
one and not the other fails the build rather than diverging quietly.

Money is `int` paise throughout. There is no float in this file and there never
will be — the money lint refuses one.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from openstore.sidecar.core.codes import AvailabilityBucket, OrderStatus


class Strict(BaseModel):
    """Forbid unknown fields everywhere. A Merchant that invents a field is
    telling the sidecar something it has not agreed to read."""

    model_config = ConfigDict(extra="forbid", frozen=True)


# ── Catalogue ────────────────────────────────────────────────────────────────


class ProductGroup(Strict):
    """Presentation only. Never sellable, never reserved, never a cart line."""

    id: str
    slug: str
    name: str
    description: str = ""
    media: list[str] = Field(default_factory=list)
    option_axes: dict[str, list[str]] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    status: str = "active"


class CatalogueItem(Strict):
    """The sellable unit: one resolved combination of options. Price, stock and
    threshold live here and never on the group — one count for "the tote" would
    let black selling out mark red sold out."""

    sku: str
    group_id: str
    options: dict[str, str] = Field(default_factory=dict)
    name: str
    price_minor: int = Field(ge=0)
    tags: list[str] = Field(default_factory=list)
    media: list[str] = Field(default_factory=list)
    status: str = "active"
    low_stock_threshold: int = Field(ge=0)
    hsn_sac: str
    gst_rate_bp: int = Field(ge=0, le=10000)


class Catalog(Strict):
    groups: list[ProductGroup]
    items: list[CatalogueItem]


# ── Lines, destination, contact ──────────────────────────────────────────────


class Line(Strict):
    sku: str
    qty: int = Field(ge=1)
    parent: str | None = None
    """Set only on an Add-on. Absent on an Add-on refuses `addon-without-parent`."""


class Destination(Strict):
    line1: str
    line2: str = ""
    city: str
    state: str = Field(pattern=r"^[A-Z]{2}$")
    postal_code: str = Field(pattern=r"^[0-9]{6}$")
    country: str = "IN"


class Contact(Strict):
    email: str = ""
    phone: str = ""


# ── Quote ────────────────────────────────────────────────────────────────────


class Addon(Strict):
    sku: str
    amount_minor: int = Field(ge=0)


class QuoteLine(Strict):
    """One priced row. An Add-on is a cart line but never a Quote Line: as a
    composite supply its amount folds into its parent's taxable value and
    inherits the parent's rate, HSN/SAC and Place of Supply."""

    sku: str
    qty: int = Field(ge=1)
    unit_price_minor: int = Field(ge=0)
    line_total_minor: int = Field(ge=0)
    hsn_sac: str
    gst_rate_bp: int = Field(ge=0, le=10000)
    place_of_supply: str = Field(pattern=r"^[A-Z]{2}$")
    addons: list[Addon] = Field(default_factory=list)


class DiscountLine(Strict):
    label: str
    code: str
    amount_minor: int = Field(lt=0)
    """Negative. The sign is intrinsic and never applied by a reader."""


class FulfillmentOption(Strict):
    id: str
    label: str
    cost_minor: int = Field(ge=0)
    eta_days: int = Field(ge=0)
    """A day count, never a date — a date is a clock, and no clock-derived value
    may cross door 9."""


class FulfillmentChosen(Strict):
    id: str
    cost_minor: int = Field(ge=0)


class TaxLine(Strict):
    kind: str
    label: str
    rate_bp: int = Field(ge=0, le=10000)
    amount_minor: int = Field(ge=0)
    informational: bool
    """True means the tax is already inside the subtotal and must not be added
    again. Getting this backwards double-charges every order."""


class Quote(Strict):
    currency: str = "INR"
    subtotal_minor: int = Field(ge=0)
    lines: list[QuoteLine]
    discount_lines: list[DiscountLine] = Field(default_factory=list)
    fulfillment_options: list[FulfillmentOption] = Field(default_factory=list)
    fulfillment_chosen: FulfillmentChosen
    tax_lines: list[TaxLine] = Field(default_factory=list)
    round_off_minor: int = 0
    total_minor: int = Field(ge=0)
    tax_inclusive: bool


# ── Orders ───────────────────────────────────────────────────────────────────


class OrderCreated(Strict):
    order_id: str
    order_salt_hex: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Returned HERE AND NOWHERE ELSE (§6.3a). The sidecar holds it in request
    memory and never writes it to any table, cache or log."""
    status: OrderStatus = OrderStatus.PENDING


class Order(Strict):
    order_id: str
    status: OrderStatus
    lines: list[Line]
    total_minor: int = Field(ge=0)
    refunded_minor: int = 0
    tracking_number: str | None = None
    carrier: str | None = None
    dispatched_at: str | None = None
    invoice_number: str | None = None
    expires_at: str | None = None


# ── Stock, as the agent is allowed to see it ─────────────────────────────────


class ItemAvailability(Strict):
    """What crosses an agent-facing boundary. There is no count in here, and
    that is the whole point (SPEC §5)."""

    sku: str
    bucket: AvailabilityBucket


class GroupAvailability(Strict):
    group_id: str
    bucket: AvailabilityBucket
    items: list[ItemAvailability]
