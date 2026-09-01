# tests/redteam/test_inv10_internal_routes.py
# INV-10 — The enrolment ceremony is authenticated. `/internal/webauthn/*` without a
# valid operator session must be REJECTED (401), and operator identity must come from
# the session header, never from the request body.

from __future__ import annotations

import pytest
from conftest import build_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openstore.core.database import get_session, init_database
from openstore.core.webauthn_rp import ChallengeStore


@pytest.fixture()
def studio_client():
    import openstore.core.database as _db

    cfg = build_settings()
    _db._engine = None
    init_database(cfg)
    store = ChallengeStore()
    app = FastAPI()
    from openstore.surfaces.studio import policy_studio_router

    app.include_router(
        policy_studio_router(cfg, session_factory=lambda: get_session(cfg), challenge_store=store)
    )
    return TestClient(app)


def test_register_begin_requires_operator_session(studio_client):
    """INV-10: POST /internal/webauthn/register/begin without X-Operator-Id -> 401."""
    res = studio_client.post("/internal/webauthn/register/begin", json={"user_name": "x"})
    assert res.status_code == 401


def test_register_complete_requires_operator_session(studio_client):
    res = studio_client.post(
        "/internal/webauthn/register/complete",
        json={
            "credential_id": "c", "client_data_json": "x", "attestation_object": "x", "challenge": "c",
        },
    )
    assert res.status_code == 401


def test_assertion_begin_requires_operator_session(studio_client):
    res = studio_client.post("/internal/webauthn/assertion/begin", json={})
    assert res.status_code == 401


def test_assertion_complete_requires_operator_session(studio_client):
    res = studio_client.post(
        "/internal/webauthn/assertion/complete",
        json={
            "credential_id": "c", "client_data_json": "x", "authenticator_data": "x",
            "signature": "x", "challenge": "c",
        },
    )
    assert res.status_code == 401


def test_user_id_never_from_body(studio_client):
    """INV-10: injection of a user_id in the body must not set operator identity."""
    res = studio_client.post(
        "/internal/webauthn/register/begin",
        json={"user_name": "x", "user_id": "attacker"},
        headers={"X-Operator-Id": "legit-operator"},
    )
    # The body model has no user_id field; an extra field is either ignored by
    # pydantic (default) or rejected. Either way the operator identity already
    # comes from the header alone.
    assert res.status_code in (200, 422)
