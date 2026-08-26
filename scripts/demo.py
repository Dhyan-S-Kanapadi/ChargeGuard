#!/usr/bin/env python3
"""Run the three ChargeGuard decision-path demonstrations against a local API."""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BASE_URL = os.getenv("CHARGEGUARD_API_URL", "http://127.0.0.1:8000").rstrip("/")
API_KEY = os.getenv("API_KEY")
POLL_INTERVAL_SECONDS = 0.5
POLL_TIMEOUT_SECONDS = 45


def _request(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"X-API-Key": API_KEY or ""}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(f"{BASE_URL}{path}", data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed with HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"Could not reach {BASE_URL}: {error.reason}") from error


def _wait_for_decision(chargeback_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        dispute = _request("GET", f"/disputes/{chargeback_id}")
        if dispute["status"] == "failed":
            raise RuntimeError(f"{chargeback_id} failed: {dispute.get('error')}")
        if dispute["state"].get("decision") and dispute["status"] == "completed":
            return dispute
        time.sleep(POLL_INTERVAL_SECONDS)
    raise RuntimeError(f"Timed out waiting for {chargeback_id} to finish.")


def _format_subscore(subscore: dict[str, Any] | None) -> str:
    if not subscore:
        return "unavailable"
    return f"{subscore['score']}/100 ({subscore['label']})"


def _print_result(dispute: dict[str, Any]) -> None:
    state = dispute["state"]
    flags = state.get("contradiction_flags") or []
    print(f"  decision: {state.get('decision')}")
    print(f"  win probability: {state.get('win_probability')}")
    print(f"  expected value: {state.get('expected_value')} {state.get('currency')}")
    print(
        "  third-party fraud indicators: "
        f"{_format_subscore(dispute.get('third_party_fraud_indicators'))}"
    )
    print(
        "  identity continuity: "
        f"{_format_subscore(dispute.get('identity_continuity'))}"
    )
    print(f"  contradiction flags: {'; '.join(flags) if flags else 'none'}")
    print(f"  final outcome: {state.get('final_outcome')}")


def _webhook_payload(chargeback_id: str, amount: float, *, simulate_degraded: bool = False) -> dict[str, Any]:
    return {
        "chargeback_id": chargeback_id,
        "reason_code": "10.4",
        "card_network": "VISA",
        "dispute_amount": amount,
        "currency": "USD",
        "filing_deadline": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        "merchant_id": "merchant_demo",
        "order_id": f"order_{chargeback_id}",
        "payment_id": f"pay_{chargeback_id}",
        "tracking_id": f"tracking_{chargeback_id}",
        "card_fingerprint": f"card_{chargeback_id}",
        "simulate_evidence_degraded": simulate_degraded,
    }


def _require_ready_server() -> None:
    if not API_KEY:
        raise RuntimeError("Set API_KEY to a valid local API key before running this demo.")
    health = _request("GET", "/health")
    if not health.get("model_loaded"):
        raise RuntimeError(
            "The local API has no loaded model. Train one with `poetry run python -m ml.train` "
            "and restart the server."
        )


def main() -> int:
    try:
        _require_ready_server()
        print("Creating the demo merchant.")
        _request(
            "POST",
            "/merchants",
            {
                "merchant_id": "merchant_demo",
                "name": "ChargeGuard Demo Store",
                "vertical": "ecommerce",
                "payment_provider": "stripe",
                "shipping_provider": "shiprocket",
                "freshdesk_domain": "demo.example.test",
                "average_order_value": 500.0,
                "chargeback_history_count": 0,
            },
        )

        print("\nDemonstrating a high-value authenticated and delivered case: expect FIGHT.")
        _request("POST", "/webhook/chargeback", _webhook_payload("cb_demo_fight", 1_000.0))
        fight = _wait_for_decision("cb_demo_fight")
        _print_result(fight)

        print("\nDemonstrating the same evidence on a low-value case: expect ACCEPT.")
        _request("POST", "/webhook/chargeback", _webhook_payload("cb_demo_accept", 10.0))
        accept = _wait_for_decision("cb_demo_accept")
        _print_result(accept)

        print("\nDemonstrating the degraded-evidence human-review path: expect ESCALATE_DEGRADED.")
        _request(
            "POST",
            "/webhook/chargeback",
            _webhook_payload("cb_demo_escalate", 1_000.0, simulate_degraded=True),
        )
        escalate = _wait_for_decision("cb_demo_escalate")
        _print_result(escalate)

        pdf_path = fight["state"].get("rebuttal_document_path")
        print(f"\nFIGHT rebuttal PDF: {pdf_path or 'not generated'}")
        return 0
    except RuntimeError as error:
        print(f"Demo failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
