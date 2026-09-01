"""Initial OpenStore schema (SID-3 baseline).

Revision ID: 0001
Revises: (none)
Create Date: 2026-09-01

Baseline migration: creates all tables from SQLModel metadata. This is the
schema as declared by openstore.models. Subsequent migrations (0002+) alter
it forward; each has a real down_revision pointing at its immediate parent
(SID-6 forward/down rollback).
"""

from __future__ import annotations

import sqlmodel  # noqa: F401  (register models)
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    from sqlmodel import SQLModel

    bind = op.get_bind()
    SQLModel.metadata.create_all(bind=bind)


def downgrade() -> None:
    from sqlmodel import SQLModel

    bind = op.get_bind()
    SQLModel.metadata.drop_all(bind=bind)