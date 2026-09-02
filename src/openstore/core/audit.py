# OpenStore core — audit logging (INV-12)

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session

from openstore.models import AuditLog


def audit_log(
    session: Session,
    trace_id: str,
    client_id: str,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    request_ip: str | None = None,
    user_agent: str | None = None,
    request_method: str | None = None,
    request_path: str | None = None,
    response_status: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    """
    INV-12: Audit every call with client_id + trace_id.

    Must be called for all API endpoints that touch money, policy, or identity.
    """
    entry = AuditLog(
        trace_id=trace_id,
        client_id=client_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        request_ip=request_ip,
        user_agent=user_agent,
        request_method=request_method,
        request_path=request_path,
        response_status=response_status,
        metadata=metadata,
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )

    session.add(entry)
    session.flush()

    return entry


class AuditContext:
    """Context manager for automatic audit logging of request/response."""

    def __init__(
        self,
        session: Session,
        trace_id: str,
        client_id: str,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        request_ip: str | None = None,
        user_agent: str | None = None,
        request_method: str | None = None,
        request_path: str | None = None,
    ):
        self.session = session
        self.trace_id = trace_id
        self.client_id = client_id
        self.action = action
        self.resource_type = resource_type
        self.resource_id = resource_id
        self.request_ip = request_ip
        self.user_agent = user_agent
        self.request_method = request_method
        self.request_path = request_path
        self.response_status: int | None = None
        self.metadata: dict[str, Any] | None = None
        self.start_time = datetime.now(UTC).replace(tzinfo=None)

    def set_response_status(self, status: int) -> None:
        self.response_status = status

    def add_metadata(self, key: str, value: Any) -> None:
        if self.metadata is None:
            self.metadata = {}
        self.metadata[key] = value

    def __enter__(self) -> AuditContext:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any | None,
    ) -> None:
        status = self.response_status or 500
        if exc_type:
            status = 500

        audit_log(
            session=self.session,
            trace_id=self.trace_id,
            client_id=self.client_id,
            action=self.action,
            resource_type=self.resource_type,
            resource_id=self.resource_id,
            request_ip=self.request_ip,
            user_agent=self.user_agent,
            request_method=self.request_method,
            request_path=self.request_path,
            response_status=status,
            metadata=self.metadata,
        )


def get_audit_trail(
    session: Session,
    trace_id: str | None = None,
    client_id: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AuditLog]:
    """Query audit logs with filters."""
    from sqlmodel import and_, select

    query = select(AuditLog).order_by(AuditLog.created_at.desc())  # type: ignore[attr-defined]

    conditions = []
    if trace_id:
        conditions.append(AuditLog.trace_id == trace_id)
    if client_id:
        conditions.append(AuditLog.client_id == client_id)
    if action:
        conditions.append(AuditLog.action == action)
    if resource_type:
        conditions.append(AuditLog.resource_type == resource_type)

    if conditions:
        query = query.where(and_(*conditions))

    query = query.limit(limit).offset(offset)
    return list(session.exec(query).all())
