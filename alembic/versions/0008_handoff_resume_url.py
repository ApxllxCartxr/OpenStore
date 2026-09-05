"""Add resume_url to handoffs (S12 step 8: federated signing-complete bridge).

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-05

A federated buyer process (buyer_cli.py) cannot reach studio.py's in-process
resume_after_signing when it lives in a different process from the merchant
it just signed a policy with. resume_url is where the merchant POSTs a
best-effort, no-authority "some buyer may have finished signing" hint once
the handoff is consumed — see surfaces/buyer_internal.py for how the
receiving side re-verifies before acting on it.

Nullable: existing rows and the legacy single-process path (studio.py's
resume_after_signing, gated on config.discord.buyer_bot_enabled) are
unaffected.

Mirrors 0006_psp_payment_id.py's idempotent add-column pattern: a fresh DB
already gets this column via SQLModel.metadata.create_all, so upgrade() is a
no-op there and real DDL on an existing DB.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | None = None
depends_on: tuple[str, ...] | None = None

_TABLE = "handoffs"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    columns = {c["name"] for c in inspector.get_columns(_TABLE)}
    if "resume_url" not in columns:
        op.add_column(
            _TABLE,
            sa.Column("resume_url", sa.String(length=1024), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    columns = {c["name"] for c in inspector.get_columns(_TABLE)}
    if "resume_url" in columns:
        op.drop_column(_TABLE, "resume_url")
