"""Add checkouts.discord_message_id (Q-032a: edit pay message in place).

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-10

Nullable: existing rows and non-chat checkouts leave it unset. Follows the
0006/0008 idempotent add-column pattern.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | None = None
depends_on: tuple[str, ...] | None = None

_TABLE = "checkouts"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    columns = {c["name"] for c in inspector.get_columns(_TABLE)}
    if "discord_message_id" not in columns:
        op.add_column(
            _TABLE,
            sa.Column("discord_message_id", sa.String(length=64), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    columns = {c["name"] for c in inspector.get_columns(_TABLE)}
    if "discord_message_id" in columns:
        op.drop_column(_TABLE, "discord_message_id")
