"""One id per request, carried into every log line and every refusal.

A Merchant reading `cap-exceeded` in the console has no way to ask *which*
call — the reason code names the rule and nothing names the attempt. With one
id on the refusal the agent received, on the console row the operator is
looking at, and on every log line the request produced, "why was my order
refused" stops being an investigation.

**Request-scoped, via a context variable.** The alternative is threading an id
through `decide()`, the trait client and the provider driver as a parameter,
which would put a logging concern in the signature of every money function and
would be forgotten by the first caller that did not need it. A ContextVar is
set once at the edge and read wherever a message is built.

The id is generated here and **never** taken from the caller. An agent-supplied
id would be an agent-supplied log key: two requests could claim to be the same
one, and a merchant chasing an incident would find another party's entries
mixed into it. An inbound `x-request-id` is echoed back in a separate header so
a caller can still stitch its own traces to ours without either side trusting
the other's ids.
"""

from __future__ import annotations

import secrets
from contextvars import ContextVar

#: 64 bits, hex. Long enough that ids do not collide inside a retention window,
#: short enough to read aloud off a console row.
_ID_BYTES = 8

_current: ContextVar[str] = ContextVar("openstore_request_id", default="")


def new_request_id() -> str:
    return f"req_{secrets.token_hex(_ID_BYTES)}"


def set_request_id(request_id: str) -> None:
    _current.set(request_id)


def request_id() -> str:
    """The current request's id, or `""` outside a request.

    Empty rather than a freshly minted one: a sweeper tick or a boot-time log
    line genuinely has no request behind it, and inventing an id there would
    produce entries that look correlatable and correlate to nothing.
    """
    return _current.get()


__all__ = ["new_request_id", "request_id", "set_request_id"]
