"""idempotency keys and the order-event queue

Revision ID: b1f4c7a92e30
Revises: 97715e29a1db
Create Date: 2026-09-23 18:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b1f4c7a92e30"
down_revision: str | None = "97715e29a1db"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """One row per (key, agent), so a retry returns the first answer.

    The composite primary key is the isolation: two agents sending `retry-1`
    are two different requests, and a key that were unique on its own would let
    one agent replay another's basket by guessing a string.

    No arguments are stored — only a digest of them. This table answers exactly
    one question ("same request?"), and a Destination kept here would be PII
    living outside the sealed receipt's commitment scheme (ADR-0011).
    """
    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("agent_id", sa.String(length=128), nullable=False),
        sa.Column("tool", sa.String(length=64), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("response", sa.Text(), nullable=False),
        sa.Column("stored_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key", "agent_id"),
    )
    # The sweeper deletes by age, so age is what it scans.
    op.create_index("ix_idempotency_stored_at", "idempotency_keys", ["stored_at"])

    # The event queue. Rows rather than a fire-and-forget POST: an event system
    # that drops on a restart is one nobody can build on, because an agent that
    # missed `paid` has to poll anyway — and then the polling this exists to
    # remove never actually goes away.
    op.create_table(
        "order_events",
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("agent_id", sa.String(length=128), nullable=False),
        sa.Column("order_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("callback_url", sa.String(length=2048), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    # What the sweeper asks for on every tick: due work, oldest first.
    op.create_index("ix_order_events_due", "order_events", ["state", "next_attempt_at"])
    op.create_index("ix_order_events_order", "order_events", ["order_id"])


def downgrade() -> None:
    op.drop_index("ix_order_events_order", table_name="order_events")
    op.drop_index("ix_order_events_due", table_name="order_events")
    op.drop_table("order_events")
    op.drop_index("ix_idempotency_stored_at", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
