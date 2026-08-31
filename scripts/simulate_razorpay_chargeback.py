"""Run a local Razorpay-shaped dispute simulation against ChargeGuard."""

import argparse
import os
import time

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--outcome", choices=["won", "lost"], default=None)
    args = parser.parse_args()
    base_url = os.getenv("CHARGEGUARD_API_URL", "http://127.0.0.1:8000").rstrip("/")
    api_key = os.getenv("API_KEY")
    if not api_key:
        raise SystemExit("API_KEY is required.")
    headers = {"X-API-Key": api_key}
    merchant = {
        "merchant_id": "merchant_razorpay_demo",
        "name": "Razorpay Demo Merchant",
        "vertical": "ecommerce",
        "payment_provider": "razorpay",
        "razorpay_account_id": "acc_SIMULATED_RAZORPAY",
        "freshdesk_domain": "",
        "average_order_value": 2500,
        "chargeback_history_count": 0,
    }
    with httpx.Client(timeout=15) as client:
        response = client.post(f"{base_url}/merchants", json=merchant, headers=headers)
        if response.status_code not in {201, 409}:
            response.raise_for_status()
        created = client.post(
            f"{base_url}/dev/razorpay-simulator/disputes",
            json={
                "merchant_id": merchant["merchant_id"], "payment_id": "pay_SIM_DEMO",
                "order_id": "order_SIM_DEMO", "payment_amount_paise": 250000,
                "dispute_amount_paise": 250000, "currency": "INR", "method": "upi",
                "card_network": "VISA", "network_reason_code": "10.4",
                "razorpay_reason_code": "unauthorised_transaction",
            }, headers=headers,
        )
        created.raise_for_status()
        result = created.json()
        dispute_id = result["dispute_id"]
        print(f"Created {dispute_id} from event {result['event_id']}")
        for _ in range(20):
            detail = client.get(f"{base_url}/disputes/{dispute_id}", headers=headers)
            if detail.status_code == 200:
                state = detail.json()["state"]
                if state.get("decision"):
                    print(f"Decision: {state['decision']}")
                    print(f"Win probability: {state.get('win_probability')}")
                    print(f"Expected value: {state.get('expected_value')}")
                    print(f"Evidence degraded: {state.get('evidence_collection_degraded')}")
                    print(f"Rebuttal path: {state.get('rebuttal_document_path')}")
                    print(f"Filing confirmation: {state.get('filing_confirmation')}")
                    break
            time.sleep(1)
        if args.outcome:
            client.post(f"{base_url}/dev/razorpay-simulator/disputes/{dispute_id}/transition", json={"state": "under_review"}, headers=headers).raise_for_status()
            outcome = client.post(f"{base_url}/dev/razorpay-simulator/disputes/{dispute_id}/transition", json={"state": args.outcome}, headers=headers)
            outcome.raise_for_status()
            print(f"Transitioned {dispute_id} to {args.outcome}.")


if __name__ == "__main__":
    main()
