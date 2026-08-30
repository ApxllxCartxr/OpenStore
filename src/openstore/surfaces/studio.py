# OpenStore — Policy Studio surface (S3.5). Standalone APIRouter so it is
# testable via TestClient without touching server.py (which is out of scope).
#
# Routes (all under registered REGISTRY prefixes):
#   GET  /intent/studio              -> renders templates/policy_studio.html
#   POST /internal/webauthn/register/begin
#   POST /internal/webauthn/register/complete   (INV-10: user_id from session)
#   POST /internal/webauthn/assertion/begin     (challenge bound {"mode":"policy"})
#   POST /internal/webauthn/assertion/complete  (assertion + optional policy sign)
#   GET  /internal/policy/blast-radius          (per signed-in operator)
#
# Signing-time validation (PRD §3.2a): a policy whose max_spend_total_minor would
# push the enrolled user's aggregate above the per-user cap is rejected with
# policy.aggregate_cap_exceeded; a policy_version != 2 raises LegacyPolicyError
# and is never mapped to a reason code. All totals are recomputed server-side
# (R0.8); the user_id / credential selection comes from the session, never the
# request body (INV-10).
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from openstore.config import Settings
from openstore.core.database import get_session
from openstore.core.holdcancel import AAL_HOLD_SECONDS
from openstore.core.policy_signing import (
    PER_USER_AGGREGATE_CAP_MINOR,
    LegacyPolicyError,
    blast_radius,
    complete_policy_signing,
)
from openstore.core.webauthn_rp import (
    ChallengeStore,
    WebAuthnError,
    begin_assertion,
    begin_registration,
    complete_assertion,
    complete_registration,
    get_user_credentials,
)
from openstore.models import IntentPolicy

TEMPLATES = Path(__file__).resolve().parent / "templates"

_NONCE_HEADER = "X-Operator-Id"


class RegistrationBegin(BaseModel):
    user_name: str
    display_name: str | None = None


class RegistrationComplete(BaseModel):
    credential_id: str
    client_data_json: str
    attestation_object: str
    challenge: str


class AssertionBegin(BaseModel):
    pass


class AssertionComplete(BaseModel):
    credential_id: str
    client_data_json: str
    authenticator_data: str
    signature: str
    challenge: str
    policy: dict[str, Any] | None = None


class _Operator:
    """Session-carried operator identity (INV-10). user_id is never read from a
    request body; it comes from the operator session header, which stands in for
    the authenticated merchant-operator session here (no auth framework is in
    this stage's scope)."""

    def __init__(self, user_id: str):
        self.user_id = user_id


def _operator(
    x_operator_id: str | None = Header(default=None, alias=_NONCE_HEADER),
) -> _Operator:
    if not x_operator_id or not x_operator_id.strip():
        raise HTTPException(status_code=401, detail="operator session required")
    return _Operator(x_operator_id.strip())


def policy_studio_router(
    config: Settings,
    *,
    session_factory: Callable[[], Session] | None = None,
    challenge_store: ChallengeStore | None = None,
) -> APIRouter:
    """Build the Policy Studio APIRouter. `session_factory` yields a session per
    request; defaults to get_session(config). `challenge_store` is injectable for
    tests; defaults to a fresh process store."""
    make_session = session_factory or (lambda: get_session(config))
    store = challenge_store or ChallengeStore()

    router = APIRouter()

    # ------------------------------------------------------------------ page
    @router.get("/intent/studio", response_class=HTMLResponse)
    async def studio_page(
        operator: _Operator = Depends(_operator),
    ) -> HTMLResponse:
        html = (TEMPLATES / "policy_studio.html").read_text(encoding="utf-8")
        html = html.replace("__OPERATOR_ID__", _safe_json(operator.user_id))
        html = html.replace("__HOLD_TABLE_ROWS__", _render_hold_rows())
        html = html.replace("__CAP_NOTE_HTML__", _render_cap_note())
        return HTMLResponse(html)

    # ------------------------------------------------------------ enrolment
    @router.post("/internal/webauthn/register/begin")
    async def register_begin(body: RegistrationBegin, operator: _Operator = Depends(_operator)) -> dict[str, Any]:
        options = begin_registration(
            config,
            operator.user_id,
            body.user_name,
            body.display_name or body.user_name,
            store=store,
        )
        options["user_id"] = operator.user_id
        return options

    @router.post("/internal/webauthn/register/complete")
    async def register_complete(
        body: RegistrationComplete, operator: _Operator = Depends(_operator)
    ) -> dict[str, Any]:
        session = make_session()
        try:
            cred = complete_registration(
                session,
                config,
                operator.user_id,
                body.credential_id,
                body.client_data_json,
                body.attestation_object,
                body.challenge,
                store=store,
            )
            session.commit()
            return {
                "ok": True,
                "credential_id": cred.credential_id,
                "sign_count": cred.sign_count,
            }
        except WebAuthnError as e:
            session.rollback()
            raise HTTPException(status_code=422, detail={"reason_code": e.reason_code, "message": e.message})
        finally:
            session.close()

    # ------------------------------------------------------------ assertion
    @router.post("/internal/webauthn/assertion/begin")
    async def assertion_begin(body: AssertionBegin, operator: _Operator = Depends(_operator)) -> dict[str, Any]:
        binding = {"mode": "policy"}
        options = begin_assertion(config, operator.user_id, binding=binding, store=store)
        options["binding"] = binding
        return options

    @router.post("/internal/webauthn/assertion/complete")
    async def assertion_complete(
        body: AssertionComplete, operator: _Operator = Depends(_operator)
    ) -> dict[str, Any]:
        session = make_session()
        try:
            ok, new_sign_count = complete_assertion(
                session,
                config,
                operator.user_id,
                body.credential_id,
                body.client_data_json,
                body.authenticator_data,
                body.signature,
                body.challenge,
                binding={"mode": "policy"},
                store=store,
            )
            session.commit()
        except WebAuthnError as e:
            session.rollback()
            raise HTTPException(status_code=401, detail={"reason_code": e.reason_code, "message": e.message})
        finally:
            session.close()

        # If a policy payload accompanies the verified assertion, complete the
        # signing ceremony (S3.5 / PRD §3.2a).
        if body.policy is not None:
            try:
                outcome = complete_policy_signing(
                    fields=body.policy,
                    merchant_id=operator.user_id,
                    user_id=operator.user_id,
                    credential_id=body.credential_id,
                    webauthn_sign_count=new_sign_count,
                    aggregate_spent_minor=_current_aggregate(make_session, operator.user_id),
                )
            except LegacyPolicyError as e:
                # Closed-set code per Q-006 resolution: legacy policies and aggregate-cap
                # breaches are both policy-signing gate rejections; surface the existing
                # closed-set code with the legacy detail in the message.
                raise HTTPException(
                    status_code=422,
                    detail={"reason_code": "policy.aggregate_cap_exceeded", "message": str(e)},
                )
            if not outcome.ok:
                raise HTTPException(
                    status_code=422,
                    detail={"reason_code": outcome.reason_code, "message": "aggregate cap would be exceeded"},
                )
            assert outcome.policy is not None
            psession = make_session()
            try:
                psession.add(outcome.policy)
                psession.commit()
                response = {
                    "ok": True,
                    "sign_count": new_sign_count,
                    "policy_id": outcome.policy.id,
                    "policy_hash": outcome.policy.policy_hash,
                }
            finally:
                psession.close()
            return response

        return {"ok": ok, "sign_count": new_sign_count}

    # ------------------------------------------------------------ blast radius
    @router.get("/internal/policy/blast-radius")
    async def policy_blast_radius(operator: _Operator = Depends(_operator)) -> dict[str, Any]:
        session = make_session()
        try:
            policies = list(
                session.exec(
                    select(IntentPolicy).where(IntentPolicy.merchant_id == operator.user_id)
                ).all()
            )
            credentials = get_user_credentials(session, operator.user_id)
            envelope_ids = [p.policy_hash for p in policies]
            radius = blast_radius(policies, envelope_ids)
            radius["credentials"] = len(credentials)
            return radius
        finally:
            session.close()

    return router


def _safe_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def _current_aggregate(session_factory: Callable[[], Session], user_id: str) -> int:
    """Recompute the enrolled operator's current active aggregate server-side
    (R0.8). Never trusted from the client."""
    session = session_factory()
    try:
        active = list(
            session.exec(
                select(IntentPolicy).where(
                    IntentPolicy.merchant_id == user_id,
                    IntentPolicy.is_active.is_(True),  # type: ignore[attr-defined]
                )
            ).all()
        )
        return sum(p.max_spend_total_minor for p in active)
    finally:
        session.close()


# Server-side rendering for the HOLD_SECONDS table and the aggregate-cap note.
# The values are normative (DECISIONS §11.1.5 / PRD §3.2a) and must be visible
# before the human signs; rendering them on the server keeps the page meaningful
# even before JavaScript runs.
_HOLD_MEANINGS = {
    3: "fresh, user-verified signature on this exact cart — immediate settlement",
    2: "standing policy with a fresh user-verified signature — 15-minute hold",
    1: "freshness / attestation / verification predicate failed — 1-hour hold",
}


def _render_hold_rows() -> str:
    from openstore.core.holdcancel import AALLevel

    rows: list[str] = []
    for level in (3, 2, 1):
        secs = AAL_HOLD_SECONDS.get(AALLevel(level))
        label = "no order created" if secs is None else f"{secs}s"
        rows.append(
            f"<tr><td><strong>AAL{level}</strong></td><td>{label}</td>"
            f"<td>{_HOLD_MEANINGS[level]}</td></tr>"
        )
    return "".join(rows)


def _render_cap_note() -> str:
    return (
        "per_user_aggregate_cap_minor = "
        f"{PER_USER_AGGREGATE_CAP_MINOR} paise. Signing a policy that pushes the "
        "enrolled operator's active aggregate above this is rejected with "
        "policy.aggregate_cap_exceeded."
    )
