"""Add handoffs table (S11 Phase 2 / Q-014).

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-03

The `handoffs` table parks a chat conversation while a human completes an
out-of-band ceremony (policy signing, amendment approval) in a browser, then
resumes it (docs/SPECS/stage-11-chat-flow.md, plan
go-with-the-push-functional-hearth.md).

0001 uses SQLModel.metadata.create_all, so a fresh DB already gets `handoffs`
via 0001 (openstore.models.Handoff is now part of the metadata) before this
revision ever runs. An existing DB that was migrated up to 0002 before this
table existed does NOT have it yet, so this revision must create it there.
upgrade() checks for the table's existence first so it is a no-op on a fresh
DB and a real DDL change on an existing one — never a collision either way.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: tuple[str, ...] | None = None

_TABLE = "handoffs"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE in inspector.get_table_names():
        return

    op.create_table(
        _TABLE,
        sa.Column("token", sa.String(length=64), primary_key=True),
        sa.Column("kind", sa.Enum("policy", "amendment", name="handoffkind"), nullable=False),
        sa.Column("merchant_id", sa.String(length=64), nullable=False),
        sa.Column("chat_platform", sa.String(length=32), nullable=False),
        sa.Column("chat_user_id", sa.String(length=64), nullable=False),
        sa.Column("chat_channel_id", sa.String(length=64), nullable=False),
        sa.Column("request_text", sa.String(length=4096), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("result_policy_id", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_handoffs_merchant_id", _TABLE, ["merchant_id"])
    op.create_index("ix_handoffs_chat_user_id", _TABLE, ["chat_user_id"])
    op.create_index("ix_handoff_chat_user", _TABLE, ["chat_platform", "chat_user_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return
    op.drop_index("ix_handoff_chat_user", table_name=_TABLE)
    op.drop_index("ix_handoffs_chat_user_id", table_name=_TABLE)
    op.drop_index("ix_handoffs_merchant_id", table_name=_TABLE)
    op.drop_table(_TABLE)
