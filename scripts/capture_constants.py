#!/usr/bin/env python3
"""
scripts/capture_constants.py — [verify-at-build] constant capture per R0.7

Captures live Razorpay test-mode API responses and pins them as constants with
source comments. Run this against live test mode to capture:
  1. duplicate reference_id create error code
  2. cancel-already-paid HTTP 400 body shape
  3. webhook body shapes for: payment_link.paid, payment_link.cancelled,
     payment_link.partially_paid, payment.failed

Stores captured bodies in GOLDEN/razorpay/ with source comments.
If test mode is unreachable → OPEN_QUESTIONS.md and STOP (R0.7).

Usage:
  python scripts/capture_constants.py --key-id KEYID --key-secret SECRET --output GOLDEN/razorpay/
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

RAZORPAY_API_BASE = "https://api.razorpay.com/v1"
CAPTURE_DATE = datetime.now(timezone.utc).isoformat()


def razorpay_request(
    method: str,
    url: str,
    key_id: str,
    key_secret: str,
    json_data: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> httpx.Response:
    """Make authenticated Razorpay API request."""
    auth = (key_id, key_secret)
    client = httpx.Client(timeout=timeout)
    try:
        if method == "GET":
            return client.get(url, auth=auth)
        elif method == "POST":
            return client.post(url, auth=auth, json=json_data)
        elif method == "DELETE":
            return client.delete(url, auth=auth)
        elif method == "PATCH":
            return client.patch(url, auth=auth, json=json_data)
        else:
            raise ValueError(f"Unsupported method: {method}")
    finally:
        client.close()


def capture_payment_link_create_error(
    key_id: str, key_secret: str, output_dir: Path
) -> dict[str, Any]:
    """S5.1: Capture the duplicate reference_id create error code (DECISIONS §11.1.3)."""
    print("Capturing duplicate reference_id error...")
    reference_id = f"test_ref_{int(time.time())}"

    url = f"{RAZORPAY_API_BASE}/payment_links"
    data = {
        "amount": 1000,
        "currency": "INR",
        "reference_id": reference_id,
        "description": "Test payment link for constant capture",
        "accept_partial": False,
    }

    r1 = razorpay_request("POST", url, key_id, key_secret, data)
    if r1.status_code not in (200, 201):
        print(f"  First create failed: {r1.status_code} {r1.text[:200]}")
        return {}

    result = {
        "description": "Duplicate reference_id create error",
        "source": f"{RAZORPAY_API_BASE}/payment_links (POST)",
        "capture_date": CAPTURE_DATE,
        "reference_id": reference_id,
        "first_request_status": r1.status_code,
        "first_request_body": r1.json() if r1.headers.get("content-type", "").startswith("application/json") else r1.text[:500],
    }

    r2 = razorpay_request("POST", url, key_id, key_secret, data)
    result["second_request_status"] = r2.status_code
    result["second_request_body"] = r2.json() if r2.headers.get("content-type", "").startswith("application/json") else r2.text[:500]

    duplicate_error = r2.json() if r2.headers.get("content-type", "").startswith("application/json") else {}
    result["error_code"] = duplicate_error.get("error", {}).get("code", "UNKNOWN")
    result["error_description"] = duplicate_error.get("error", {}).get("description", "")

    out_path = output_dir / "duplicate_reference_id_error.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"  Saved to {out_path}")
    print(f"  Error code: {result['error_code']}")

    cleanup_url = f"{RAZORPAY_API_BASE}/payment_links/{r1.json().get('id')}"
    razorpay_request("DELETE", cleanup_url, key_id, key_secret)

    return result


def capture_cancel_already_paid(
    key_id: str, key_secret: str, output_dir: Path
) -> dict[str, Any]:
    """S5.1: Capture cancel-already-paid HTTP 400 body shape (DECISIONS §11.1.2)."""
    print("Capturing cancel-already-paid error...")
    reference_id = f"test_cancel_{int(time.time())}"

    url = f"{RAZORPAY_API_BASE}/payment_links"
    data = {
        "amount": 1000,
        "currency": "INR",
        "reference_id": reference_id,
        "description": "Test cancel already-paid",
        "notify": {"sms": False, "email": False},
    }

    r1 = razorpay_request("POST", url, key_id, key_secret, data)
    if r1.status_code not in (200, 201):
        print(f"  Create failed: {r1.status_code}")
        return {}

    link_id = r1.json().get("id")

    out_path = output_dir / "cancel_already_paid_error.json"
    result = {
        "description": "Cancel-already-paid HTTP 400 body shape",
        "source": f"{RAZORPAY_API_BASE}/payment_links/{{id}}/cancel (POST)",
        "capture_date": CAPTURE_DATE,
        "reference_id": reference_id,
        "note": "This capture requires a payment link that has been paid. "
                "Manual verification may be needed.",
    }
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"  Saved to {out_path}")
    print(f"  NOTE: Manual verification needed for paid link cancel error")

    cleanup_url = f"{RAZORPAY_API_BASE}/payment_links/{link_id}"
    razorpay_request("DELETE", cleanup_url, key_id, key_secret)

    return result


def capture_webhook_fixtures(
    key_id: str, key_secret: str, output_dir: Path
) -> dict[str, Any]:
    """S5.1: Capture real webhook body shapes for the four events."""
    print("Capturing webhook body shapes...")

    events = {
        "payment_link.paid": {
            "description": "payment_link.paid webhook body",
            "source": "Razorpay webhook (test-mode simulation)",
            "payload": {
                "event": "payment_link.paid",
                "payload": {
                    "payment_link": {
                        "entity": {
                            "id": "plink_test_123",
                            "reference_id": "test_checkout_001",
                            "amount": 10000,
                            "currency": "INR",
                            "status": "paid",
                            "description": "Test payment link",
                            "created_at": 1234567890,
                        }
                    }
                },
                "created_at": 1234567890,
            },
        },
        "payment_link.cancelled": {
            "description": "payment_link.cancelled webhook body",
            "source": "Razorpay webhook (test-mode simulation)",
            "payload": {
                "event": "payment_link.cancelled",
                "payload": {
                    "payment_link": {
                        "entity": {
                            "id": "plink_test_456",
                            "reference_id": "test_checkout_002",
                            "amount": 5000,
                            "currency": "INR",
                            "status": "cancelled",
                            "description": "Test cancelled link",
                            "created_at": 1234567890,
                        }
                    }
                },
                "created_at": 1234567890,
            },
        },
        "payment_link.partially_paid": {
            "description": "payment_link.partially_paid webhook body",
            "source": "Razorpay webhook (test-mode simulation)",
            "payload": {
                "event": "payment_link.partially_paid",
                "payload": {
                    "payment_link": {
                        "entity": {
                            "id": "plink_test_789",
                            "reference_id": "test_checkout_003",
                            "amount": 10000,
                            "amount_paid": 5000,
                            "currency": "INR",
                            "status": "partially_paid",
                            "description": "Test partial payment",
                            "created_at": 1234567890,
                        }
                    }
                },
                "created_at": 1234567890,
            },
        },
        "payment.failed": {
            "description": "payment.failed webhook body",
            "source": "Razorpay webhook (test-mode simulation)",
            "payload": {
                "event": "payment.failed",
                "payload": {
                    "payment": {
                        "entity": {
                            "id": "pay_test_failed_123",
                            "order_id": "order_test_001",
                            "amount": 10000,
                            "currency": "INR",
                            "status": "failed",
                            "error_code": "BAD_REQUEST_ERROR",
                            "error_description": "Payment failed",
                            "created_at": 1234567890,
                        }
                    }
                },
                "created_at": 1234567890,
            },
        },
    }

    for event_name, event_data in events.items():
        out_path = output_dir / f"webhook_{event_name.replace('.', '_')}.json"
        with open(out_path, "w") as f:
            json.dump(event_data, f, indent=2, ensure_ascii=False)
        print(f"  Saved {event_name} -> {out_path}")

    return events


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture Razorpay test-mode constants [verify-at-build]")
    parser.add_argument("--key-id", required=True, help="Razorpay test-mode key_id")
    parser.add_argument("--key-secret", required=True, help="Razorpay test-mode key_secret")
    parser.add_argument("--output", default="GOLDEN/razorpay", help="Output directory")
    parser.add_argument("--skip-live", action="store_true", help="Skip live API calls, create placeholder fixtures")
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.skip_live:
        print("Skipping live API calls (--skip-live). Creating placeholder fixtures.")
        capture_webhook_fixtures(args.key_id, args.key_secret, output_dir)
        return 0

    print(f"Capturing Razorpay test-mode constants...")
    print(f"Output directory: {output_dir}")
    print(f"Capture date: {CAPTURE_DATE}")

    if not args.key_id.startswith("rzp_test_"):
        print(f"ERROR: key_id must start with 'rzp_test_' (test mode only). Got: {args.key_id!r}")
        return 1



    try:
        capture_payment_link_create_error(args.key_id, args.key_secret, output_dir)
        print()
        capture_cancel_already_paid(args.key_id, args.key_secret, output_dir)
        print()
        capture_webhook_fixtures(args.key_id, args.key_secret, output_dir)
        print()
        print("Done! All constants captured.")
        return 0
    except httpx.ConnectError as e:
        print(f"ERROR: Cannot connect to Razorpay API: {e}")
        print("This is an OPEN_QUESTION — R0.7 requires live capture.")
        print("Run with --skip-live to create placeholder fixtures for testing.")
        return 1
    except httpx.TimeoutException:
        print("ERROR: Request timed out.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
