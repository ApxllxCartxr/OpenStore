"""Refusals crossing the trait boundary, carrying a closed reason code.

Every refusal is `{"error": {"code", "detail", "fields"}}` and the HTTP status
is derived from the code by the one table in `core/codes.py` (§6.1). Two
implementers choosing statuses independently is how one client ends up retrying
what the other treats as fatal.
"""

from __future__ import annotations

from typing import Any

from openstore.sidecar.core.codes import ReasonCode, http_status


class TraitError(Exception):
    """A door refused, and named why.

    Constructed with a `ReasonCode` — never a string. A code invented at a call
    site is a red build by design; a code added to the registry is a decision.
    """

    def __init__(
        self,
        code: ReasonCode,
        detail: str,
        fields: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code.value}: {detail}")
        self.code = code
        self.detail = detail
        self.fields = fields or {}

    @property
    def status_code(self) -> int:
        return http_status(self.code)

    def to_payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code.value, "detail": self.detail}
        if self.fields:
            body["fields"] = self.fields
        return {"error": body}

    @classmethod
    def from_payload(cls, payload: Any, status_code: int) -> TraitError:
        """Parse a refusal a Merchant sent back.

        Fails loud on a malformed envelope rather than inventing a code: a
        Merchant that answers an error with something else has a bug the sidecar
        must not paper over, and `not-found` guessed from a 404 would be a
        fabricated reason code in signed evidence.
        """
        if not isinstance(payload, dict) or not isinstance(payload.get("error"), dict):
            raise TraitProtocolError(
                f"HTTP {status_code} with no error envelope — a refusal must carry "
                f"{{'error': {{'code', 'detail'}}}}; got {payload!r}"
            )
        raw = payload["error"].get("code")
        try:
            code = ReasonCode(raw)
        except ValueError:
            raise TraitProtocolError(
                f"HTTP {status_code} carried reason code {raw!r}, which is in no closed "
                f"set. A code that exists only at a call site is a bug in the Merchant."
            ) from None
        return cls(code, str(payload["error"].get("detail", "")), payload["error"].get("fields"))


class TraitProtocolError(Exception):
    """The Merchant did not speak the trait: an unparseable body, a code outside
    the closed set, a signature that does not verify, or a mutation answered with
    a different result for the same idempotency key.

    Deliberately not a `TraitError`: it carries no reason code because it *is*
    the absence of one, and it must never be mapped into a refusal the Consumer
    sees as a normal outcome.
    """
