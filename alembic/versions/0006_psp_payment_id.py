"""Add psp_payment_id to checkouts (Q-027 / S11 Phase 3-4).

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-04

The Razorpay refund API requires a payment_id (pay_...), not a
payment_link_id (plink_...). This migration adds a nullable
psp_payment_id column to store the payment ID when the webhook
receives payment_link.paid, so refund_checkout can use it directly.

Option (c) per Q-027: primary path is the column; fallback is
fetching from payment_link at refund time.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | None = None
depends_on: tuple[str, ...] | None = None

_CHECKOUTS = "checkouts"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    checkout_columns = {c["name"] for c in inspector.get_columns(_CHECKOUTS)}
    if "psp_payment_id" not in checkout_columns:
        op.add_column(
            _CHECKOUTS,
            sa.Column("psp_payment_id", sa.String(length=64), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    checkout_columns = {c["name"] for c in inspector.get_columns(_CHECKOUTS)}
    if "psp_payment_id" in checkout_columns:
        op.drop_column(_CHECKOUTS, "psp_payment_id")
