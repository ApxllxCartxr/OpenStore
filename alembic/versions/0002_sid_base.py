"""SID base revision (SID-6 forward/down chain).

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-01

Forward: no schema change — the SID-1..7 contract adds no new tables/columns;
it governs boot ordering, readiness, origin security, versioning, and metrics.
This revision exists so the migration chain has a real forward/down step:
`upgrade` applies 0001→0002 and `downgrade` returns 0002→0001 (rollback).
The version stamp is the rollback safety net (alembic_version table).
"""

from __future__ import annotations

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    # No DDL: the SID contract is behavioral. Kept as an explicit revision so
    # the chain 0001 -> 0002 is stamped and downgradeable.
    pass


def downgrade() -> None:
    pass