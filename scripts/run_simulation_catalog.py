"""Load the dashboard catalog and verify each local simulation's stored result.

API_KEY must be set in the caller's environment. Existing demo cases are retained.
"""

import argparse
from collections import Counter
import os
import time
from urllib.parse import urlparse

import httpx


def wait_for_case(client: httpx.Client, case_id: str, last_event_id: str) -> dict:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        response = client.get(f"/disputes/{case_id}")
        if response.status_code != 404:
            response.raise_for_status()
            case = response.json()
            if case["status"] in {"completed", "failed"} and case["state"].get("provider_event_id") == last_event_id:
                assert case["status"] == "completed", "graph failed"
                return case["state"]
        time.sleep(0.25)
    raise AssertionError("graph did not finish within 90 seconds")


def check_scenario(client: httpx.Client, scenario: dict, merchant: str) -> str:
    response = client.post(f"/dev/razorpay-simulator/scenarios/{scenario['id']}/run",
                           json={"merchant_id": merchant})
    response.raise_for_status()
    run = response.json()
    statuses = [delivery["delivery"]["status_code"] for delivery in run["deliveries"]]
    behavior = scenario["behavior"]
    expected_statuses = ([401] if behavior == "invalid_signature" else
                         [202, 200] if behavior == "duplicate" else
                         [202, 202] if behavior.startswith("created_then_") else [202])
    assert statuses == expected_statuses, f"unexpected delivery statuses: {statuses}"
    case_id = run["dispute_id"]
    if behavior in {"invalid_signature", "unknown_account"}:
        deadline = time.monotonic() + 15
        event_id = run["deliveries"][0]["event_id"]
        while True:
            response = client.get("/internal/razorpay/events")
            response.raise_for_status()
            events = response.json()
            event = next((item for item in events if item["event_id"] == event_id), None)
            if behavior == "invalid_signature":
                assert event is None, "invalid signature created an event"
                break
            if event and event["processing_state"] == "unresolved":
                break
            assert time.monotonic() < deadline, "unknown account was not unresolved"
            time.sleep(0.25)
        assert client.get(f"/disputes/{case_id}").status_code == 404
        return "REJECTED" if behavior == "invalid_signature" else "UNRESOLVED"

    state = wait_for_case(client, case_id, run["deliveries"][-1]["event_id"])
    payload = scenario["payload"]
    assert state["merchant_profile"]["merchant_id"] == merchant
    assert state["dispute_amount"] == payload["dispute_amount_paise"] / 100
    assert state["currency"] == payload["currency"]
    assert state["final_outcome"] not in {"WIN", "LOSS"}
    profile = scenario.get("device")
    if profile and scenario["family"] == "Device and IP":
        risk = profile["risk"]
        evidence = state["device"]
        assert evidence["fraud_score"] == risk["fraud_score"]
        assert evidence["vpn_detected"] == risk["network"]["vpn"]
        assert evidence["geolocation_match"] == risk["geo"]["matches_shipping_region"]
        assert evidence["device_fingerprint"] == profile["device_id"]
    escalate = (scenario["family"] == "Device failures" or payload["method"] != "card"
                or payload["respond_within_hours"] < 0 or not payload["network_reason_code"]
                or payload["card_network"] == "AMEX" or behavior == "out_of_order_won")
    if escalate:
        assert state["decision"] == "ESCALATE_DEGRADED"
        assert state["filed_at"] is None
        if scenario["family"] == "Device failures":
            assert state["device"] is None
            assert "device_provider_unavailable" in state["degraded_reasons"]
    else:
        assert state["transaction"]["amount"] == payload["payment_amount_paise"] / 100
        assert state["decision"] in {"FIGHT", "ACCEPT"}
        if scenario["id"] == "low-value-delivered":
            assert state["decision"] == "ACCEPT"
        if scenario["id"] == "friendly-fraud-high-value":
            assert state["decision"] == "FIGHT"
        if state["decision"] == "FIGHT":
            assert state["quality_approved"] and state["filed_at"]
            assert state["filing_confirmation"].startswith("filed_")
        else:
            assert state["final_outcome"] == "ACCEPTED_NO_CONTEST"
            assert state["filed_at"] is None
    if behavior == "created_then_action_required":
        assert state["provider_action_required"] is True
    if behavior == "created_then_under_review":
        assert state["provider_action_required"] is False
    return state["decision"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8200")
    parser.add_argument("--merchant", default="merchant_demo")
    args = parser.parse_args()
    parsed = urlparse(args.base_url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or parsed.username or parsed.password:
        parser.error("Use a loopback URL without credentials")
    key = os.getenv("API_KEY", "").split(",")[0].strip()
    if not key:
        parser.error("Set API_KEY in your environment first")
    failures = 0
    results = Counter()
    with httpx.Client(base_url=args.base_url, headers={"X-API-Key": key}, timeout=120) as client:
        response = client.get("/health")
        response.raise_for_status()
        health = response.json()
        assert health["model_loaded"] and health["stub_mode"], "Requires a model and stub mode"
        response = client.get("/dev/razorpay-simulator/scenarios")
        response.raise_for_status()
        scenarios = response.json()
        for scenario in scenarios:
            try:
                outcome = check_scenario(client, scenario, args.merchant)
                results[outcome] += 1
                print(f"PASS {scenario['id']}: {outcome}", flush=True)
            except (AssertionError, httpx.HTTPError, KeyError, TypeError) as exc:
                failures += 1
                print(f"FAIL {scenario['id']}: {type(exc).__name__}: {exc}", flush=True)
    print(f"{len(scenarios) - failures}/{len(scenarios)} passed; {dict(results)}")
    raise SystemExit(bool(failures))


if __name__ == "__main__":
    main()
