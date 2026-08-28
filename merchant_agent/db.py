"""Read-only database access for the merchant reasoning agent.

Opens its own SQLAlchemy engine against the SAME database file as the
merchant server (read access to Product/Cart/Order/AuditLogEntry data is
the whole point of the finance-Q&A skill). This module — and nothing else
in merchant_agent/ — ever calls .add() or .commit().

Write-isolation is enforced at the database layer: the engine opens the
SQLite file in read-only URI mode (`mode=ro`), so a bug or a future edit
that calls .add()/.commit() fails at the driver instead of silently
mutating merchant data. The architectural guarantee (no signing key or
Razorpay secret in this process's .env) is defense-in-depth on top.
"""

import os

from sqlmodel import SQLModel, create_engine, Session
from merchant_agent.config import settings


def _readonly_url(url: str) -> str:
    """Rewrite a sqlite:/// URL into a read-only file-URI connection string."""
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return url
    path = os.path.abspath(url[len(prefix):])
    return f"sqlite:///file:{path}?mode=ro&uri=True"


engine = create_engine(
    _readonly_url(settings.database_url),
    connect_args={"check_same_thread": False, "uri": True},
)


def get_readonly_session() -> Session:
    """Open a read-only SQLModel session.

    Callers MUST NOT call session.add() or session.commit() on this session.
    The connection is opened in SQLite read-only mode, so any write attempt
    raises at the driver regardless of caller intent.
    """
    return Session(engine)
