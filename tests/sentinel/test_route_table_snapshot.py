# tests/sentinel/test_route_table_snapshot.py
# S10.3 — Route-table snapshot: the set of routes mounted on the server must not drift
# from REGISTRY.json `routes`. Adding a route without updating the registry (or the
# registry naming a route the app does not mount) fails the build.

from __future__ import annotations

import json
from pathlib import Path

from openstore.server import create_app

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = json.loads((ROOT / "REGISTRY.json").read_text())

# Dynamic routes in REGISTRY use <param> placeholders; the FastAPI app renders
# them as {param}. Normalise both sides to a match on the static prefix/key.
_REGISTRY_TO_APP = {
    "/campaign/<campaign_id>/approve": "/campaign/{campaign_id}/approve",
    "/campaign/<campaign_id>/reject": "/campaign/{campaign_id}/reject",
    "/hold/<cancel_token>/cancel": "/hold/{cancel_token}/cancel",
    "/orders/<checkout_id>/evidence": "/orders/{checkout_id}/evidence",
    "/orders/<id>/evidence/view": "/orders/{id}/evidence/view",
    "/intent/amendment/<amendment_id>/approve": "/intent/amendment/{amendment_id}/approve",
    "/intent/amendment/<amendment_id>/reject": "/intent/amendment/{amendment_id}/reject",
}

# Routes mounted by the app that are NOT in REGISTRY.json because they are
# framework/infra/default endpoints, not agent-commerce surface routes.
_INFRA_ROUTES = {
    "/healthz",
    "/",
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
    "/webhooks/razorpay",
}

# Routes declared in REGISTRY.json that the demo server intentionally does not
# mount yet (documented per-stage entries / future scope — verified by stage
# specs, not silently dropped).
_EXPECTED_ABSENT = {
    "/protocols/<name>/spec-excerpt",
    "/admin/agents",
    "/admin/*",
    "/internal/webauthn/*",
    "/orders/intent/<intent_id>/evidence",
}


def test_registry_routes_are_a_closed_set():
    """REGISTRY.routes must be non-empty and list-shaped."""
    assert isinstance(REGISTRY["routes"], list)
    assert len(REGISTRY["routes"]) >= 15


def test_app_mounts_all_expected_routes(settings):
    app = create_app(settings)
    mounted = {r.path for r in app.routes if hasattr(r, "path")}

    missing = []
    for reg_route in REGISTRY["routes"]:
        if reg_route in _EXPECTED_ABSENT:
            continue
        expected = _REGISTRY_TO_APP.get(reg_route, reg_route)
        if expected not in mounted:
            missing.append(reg_route)

    assert missing == [], f"REGISTRY routes not mounted by the app: {missing}"


def test_no_undisclosed_routes_mounted(settings):
    """Every app route must be disclosed by REGISTRY.routes or the infra allowlist
    (no shadow endpoints)."""
    app = create_app(settings)
    mounted = {r.path for r in app.routes if hasattr(r, "path")}

    declared = set(_REGISTRY_TO_APP.values()) | set(REGISTRY["routes"])
    # Normalise registry <param> routes to {param} for exact comparison.
    declared = {
        d.replace("<", "{").replace(">", "}") if "<" in d else d for d in declared
    }

    shadow = [
        path for path in mounted - _INFRA_ROUTES
        if path not in declared
    ]
    assert shadow == [], f"App routes not declared in REGISTRY: {shadow}"
