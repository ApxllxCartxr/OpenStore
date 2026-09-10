"""Add CART handoff kind + cart_payload (S16 / Q-033: per-cart passkey ceremony).

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-10

A chat-only checkout previously graded at AAL1 at best: no ceremony could
produce a fresh per-cart WebAuthn assertion from chat. kind=CART parks the
pending cart (cart_id/cart/cart_hash/policy_id in cart_payload) while the
human taps a passkey on /intent/studio?token=... bound to
{"mode": "cart", "cart_hash": ...}; approval creates the checkout with
assertion_verified=True so the bundle can reach AAL2.

Postgres: the handoffs.kind SQLEnum is a native PG enum — ALTER TYPE ... ADD
VALUE (committed immediately, cannot run inside the migration transaction).
SQLite: no-op (VARCHAR, values unenforced). cart_payload follows the
0006/0008 idempotent add-column pattern.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | None = None
depends_on: tuple[str, ...] | None = None

_TABLE = "handoffs"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE handoffkind ADD VALUE IF NOT EXISTS 'cart'")

    columns = {c["name"] for c in inspector.get_columns(_TABLE)}
    if "cart_payload" not in columns:
        op.add_column(
            _TABLE,
            sa.Column("cart_payload", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    columns = {c["name"] for c in inspector.get_columns(_TABLE)}
    if "cart_payload" in columns:
        op.drop_column(_TABLE, "cart_payload")
    # PG enum value removal requires type recreation — deliberately not
    # implemented (removing a ceremony kind from a live DB is out of scope).
