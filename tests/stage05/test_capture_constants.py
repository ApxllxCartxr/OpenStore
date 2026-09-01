# tests/stage05/test_capture_constants.py
# Stage 5 — S5.1 capture_constants.py and golden fixtures exist + are well-formed.

from __future__ import annotations

import json
from pathlib import Path

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "GOLDEN" / "razorpay"


def test_golden_fixtures_exist():
    """All four event bodies + both pinned error shapes must exist."""
    expected = [
        "payment_link_paid.json",
        "payment_link_cancelled.json",
        "payment_link_partially_paid.json",
        "payment_failed.json",
        "error_duplicate_reference_id.json",
        "error_cancel_paid.json",
    ]
    for name in expected:
        path = GOLDEN_DIR / name
        assert path.exists(), f"Missing golden fixture: {path}"


def test_golden_fixtures_are_valid_json():
    for path in sorted(GOLDEN_DIR.glob("*.json")):
        with open(path) as f:
            payload = json.load(f)
        assert isinstance(payload, dict), f"{path.name} is not a JSON object"


def test_golden_payment_link_paid_has_required_fields():
    with open(GOLDEN_DIR / "payment_link_paid.json") as f:
        payload = json.load(f)
    entity = payload["payload"]["payment_link"]["entity"]
    assert entity["status"] == "paid"
    assert entity["reference_id"]  # INV-4: reference_id = checkout_id
    notes = entity["notes"]
    assert notes["checkout_id"] == entity["reference_id"]
    assert notes["trace_id"]
    assert notes["client_id"]


def test_golden_payment_failed_has_error_description():
    with open(GOLDEN_DIR / "payment_failed.json") as f:
        payload = json.load(f)
    payment = payload["payload"]["payment"]["entity"]
    assert payment["status"] == "failed"
    assert "error_code" in payment


def test_golden_duplicate_reference_id_has_pinned_code():
    with open(GOLDEN_DIR / "error_duplicate_reference_id.json") as f:
        body = json.load(f)
    err = body["error"]
    assert err["code"] == "BAD_REQUEST_ERROR"
    assert err["reason"] == "duplicate_reference_id"


def test_golden_cancel_paid_has_pinned_code():
    with open(GOLDEN_DIR / "error_cancel_paid.json") as f:
        body = json.load(f)
    err = body["error"]
    assert err["code"] == "BAD_REQUEST_ERROR"
    assert err["reason"] == "payment_link_inactive"


def test_capture_constants_script_present():
    script = Path(__file__).resolve().parents[2] / "scripts" / "capture_constants.py"
    assert script.exists()
    content = script.read_text()
    # Must include the test-mode prefix assertion (R0.7 + MUST NOT)
    assert "rzp_test_" in content
    # Must NOT include any hard-coded live-mode key
    assert "rzp_live_" not in content
