"""Initial schema baseline (IMPLEMENTATION_SPEC R2.1).

Tables are currently created by SQLModel.metadata.create_all at startup.
This migration establishes Alembic version tracking so future schema
changes can be managed via `alembic revision --autogenerate`.

Revision ID: 001_initial
Create Date: 2026-08-29
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No-op: tables are created by create_all at startup.
    # This migration stamps Alembic's version table so future
    # autogenerate runs have a baseline to diff against.
    pass


def downgrade() -> None:
    pass
