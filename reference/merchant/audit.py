import time
import functools
import uuid

from sqlmodel import Session
from reference.merchant.db import engine
from reference.merchant.models import AuditLogEntry
from reference.merchant.trace import emit


def audited_tool(tool_name: str):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            trace_id = str(uuid.uuid4())
            client_id = None
            try:
                result = fn(*args, **kwargs)
                client_id = kwargs.get("_client_id_for_audit")
                latency_ms = (time.perf_counter() - start) * 1000
                _write_audit(tool_name, client_id, kwargs, "ok", True, latency_ms, trace_id)
                return result
            except Exception as e:
                latency_ms = (time.perf_counter() - start) * 1000
                _write_audit(tool_name, client_id, kwargs, str(e)[:200], False, latency_ms, trace_id)
                raise
        return wrapper
    return decorator


def _write_audit(tool, client_id, args, result_summary, success, latency_ms, trace_id):
    try:
        with Session(engine) as session:
            session.add(AuditLogEntry(
                trace_id=trace_id,
                client_id=client_id,
                tool=tool,
                args_json={k: v for k, v in args.items() if k not in ("ctx", "trace_id")},
                result_summary=result_summary[:200],
                success=success,
                latency_ms=latency_ms,
            ))
            session.commit()
        emit(
            "merchant-server",
            f"{tool} {'ok' if success else 'rejected'}",
            {"client": client_id or "unknown", "result": result_summary[:100]},
            trace_id,
            "executed" if success else "blocked",
        )
    except Exception:
        pass
