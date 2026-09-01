"""Exercise the real signed Razorpay webhook endpoint with local-only scenarios."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sys
from urllib.parse import urlparse

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.razorpay_simulator import build_simulator_envelope


SCENARIOS = (
    "card-created",
    "upi-created",
    "action-required",
    "under-review",
    "won",
    "lost",
    "closed",
    "duplicate",
    "invalid-signature",
    "unknown-merchant",
    "expired",
    "out-of-order",
)


def _require_local_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise SystemExit("The simulator only targets a loopback ChargeGuard instance.")
    return value.rstrip("/")


def _send(
    client: httpx.Client,
    url: str,
    envelope: dict,
    secret: str,
    *,
    event_id: str | None = None,
    valid_signature: bool = True,
) -> httpx.Response:
    body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    signing_secret = secret if valid_signature else f"invalid-{secret}"
    signature = hmac.new(signing_secret.encode(), body, hashlib.sha256).hexdigest()
    headers = {"Content-Type": "application/json", "X-Razorpay-Signature": signature}
    if event_id:
        headers["x-razorpay-event-id"] = event_id
    return client.post(url, content=body, headers=headers)


def _record(scenario: str) -> dict:
    now = datetime.now(timezone.utc)
    is_upi = scenario == "upi-created"
    return {
        "account_id": (
            "acc_SIM_UNKNOWN" if scenario == "unknown-merchant" else "acc_SIMULATED_RAZORPAY"
        ),
        "payment_id": "pay_SIM_" + secrets.token_hex(6),
        "order_id": "order_SIM_" + secrets.token_hex(6),
        "payment_amount_paise": 250000,
        "dispute_amount_paise": 250000,
        "currency": "INR",
        "method": "upi" if is_upi else "card",
        "card_network": None if is_upi else "VISA",
        "network_reason_code": "10.4",
        "razorpay_reason_code": "unauthorised_transaction",
        "customer_email": "simulation@example.test",
        "customer_contact": None,
        "vpa": "simulation@upi" if is_upi else None,
        "dispute_id": "disp_SIM_" + secrets.token_urlsafe(12),
        "state": "open",
        "created_at": now,
        "respond_by": (
            now - timedelta(hours=1)
            if scenario == "expired"
            else now + timedelta(days=3)
        ),
        "deliveries": [],
    }


def _register_merchant(client: httpx.Client, base_url: str, api_key: str) -> None:
    response = client.post(
        f"{base_url}/merchants",
        headers={"X-API-Key": api_key},
        json={
            "merchant_id": "merchant_razorpay_simulator",
            "name": "Razorpay Simulator Merchant",
            "vertical": "ecommerce",
            "payment_provider": "razorpay",
            "razorpay_account_id": "acc_SIMULATED_RAZORPAY",
            "freshdesk_domain": "",
            "average_order_value": 2500,
            "chargeback_history_count": 0,
        },
    )
    if response.status_code not in {201, 409}:
        response.raise_for_status()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=SCENARIOS)
    parser.add_argument(
        "--base-url",
        default=os.getenv("CHARGEGUARD_API_URL", "http://127.0.0.1:8000"),
    )
    args = parser.parse_args()
    if os.getenv("ENVIRONMENT", "development").lower() == "production":
        raise SystemExit("The simulator cannot run in production.")
    if os.getenv("RAZORPAY_SIMULATOR_ENABLED", "").lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        raise SystemExit("Set RAZORPAY_SIMULATOR_ENABLED=true before running simulations.")
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET")
    api_key = os.getenv("API_KEY")
    if not secret or not api_key:
        raise SystemExit("RAZORPAY_WEBHOOK_SECRET and API_KEY are required.")

    base_url = _require_local_url(args.base_url)
    webhook_url = f"{base_url}/webhook/razorpay"
    record = _record(args.scenario)
    with httpx.Client(timeout=20) as client:
        if args.scenario != "unknown-merchant":
            _register_merchant(client, base_url, api_key)

        created = build_simulator_envelope(record, "payment.dispute.created", "open")
        if args.scenario == "invalid-signature":
            responses = [_send(client, webhook_url, created, secret, valid_signature=False)]
        elif args.scenario == "duplicate":
            event_id = "evt_SIM_DUPLICATE_" + secrets.token_hex(6)
            responses = [
                _send(client, webhook_url, created, secret, event_id=event_id),
                _send(client, webhook_url, created, secret, event_id=event_id),
            ]
        elif args.scenario == "out-of-order":
            review = build_simulator_envelope(
                record,
                "payment.dispute.under_review",
                "under_review",
            )
            created["created_at"] = review["created_at"] - 60
            responses = [
                _send(client, webhook_url, review, secret, event_id="evt_SIM_REVIEW_" + secrets.token_hex(4)),
                _send(client, webhook_url, created, secret, event_id="evt_SIM_CREATED_" + secrets.token_hex(4)),
            ]
        elif args.scenario in {"action-required", "under-review", "won", "lost", "closed"}:
            state = args.scenario.replace("-", "_")
            event = f"payment.dispute.{state}"
            lifecycle = build_simulator_envelope(record, event, state)
            responses = [
                _send(client, webhook_url, created, secret, event_id="evt_SIM_CREATED_" + secrets.token_hex(4)),
                _send(client, webhook_url, lifecycle, secret, event_id="evt_SIM_STATE_" + secrets.token_hex(4)),
            ]
        else:
            responses = [
                _send(
                    client,
                    webhook_url,
                    created,
                    secret,
                    event_id="evt_SIM_" + secrets.token_hex(8),
                )
            ]

    print(f"Scenario: {args.scenario}")
    print(f"Provider dispute: {record['dispute_id']}")
    for index, response in enumerate(responses, start=1):
        print(f"Delivery {index}: HTTP {response.status_code} {response.text}")


if __name__ == "__main__":
    main()
