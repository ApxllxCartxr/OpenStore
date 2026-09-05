"""Add shopping_sessions table (S13: conversational shopping).

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-04

Parks a buyer/LLM shopping conversation between Discord messages when
BuyerGraph's agent_step asks the buyer something instead of finalizing a
cart (e.g. "no vanilla, but we have chocolate — want that?"). See
core/shopping_session.py and openstore.models.ShoppingSession.

Mirrors 0003_handoffs.py's idempotent create pattern: a fresh DB already gets
this table via SQLModel.metadata.create_all (ShoppingSession is part of the
metadata), so upgrade() is a no-op there and real DDL on an existing DB.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | None = None
depends_on: tuple[str, ...] | None = None

_TABLE = "shopping_sessions"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE in inspector.get_table_names():
        return

    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("chat_platform", sa.String(length=32), nullable=False),
        sa.Column("chat_user_id", sa.String(length=64), nullable=False),
        sa.Column("chat_channel_id", sa.String(length=64), nullable=False),
        sa.Column("policy_id", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("goal", sa.String(length=4096), nullable=False),
        sa.Column("messages", sa.JSON(), nullable=False),
        sa.Column("turns_used", sa.Integer(), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "AWAITING_REPLY",
                "COMPLETED",
                "EXPIRED",
                "CANCELLED",
                name="shoppingsessionstate",
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_shopping_sessions_chat_user_id", _TABLE, ["chat_user_id"])
    op.create_index(
        "ix_shopping_session_chat_user",
        _TABLE,
        ["chat_platform", "chat_user_id", "chat_channel_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return
    op.drop_index("ix_shopping_session_chat_user", table_name=_TABLE)
    op.drop_index("ix_shopping_sessions_chat_user_id", table_name=_TABLE)
    op.drop_table(_TABLE)
