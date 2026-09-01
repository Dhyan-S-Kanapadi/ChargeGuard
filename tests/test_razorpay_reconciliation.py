from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from api.store import store
from main import app


@pytest.fixture(autouse=True)
def reset_store(monkeypatch):
    store.clear()
    monkeypatch.setenv("API_KEY", "test-api-key")
    yield
    store.clear()


def _merchant() -> dict:
    return {
        "merchant_id": "merchant_reconcile",
        "name": "Reconciliation Merchant",
        "vertical": "ecommerce",
        "payment_provider": "razorpay",
        "razorpay_account_id": "acc_reconcile",
        "freshdesk_domain": "",
        "average_order_value": 1000.0,
        "chargeback_history_count": 0,
    }


def test_reconciliation_is_protected_and_uses_provider_upsert(monkeypatch) -> None:
    assert store.create_merchant(_merchant())
    now = datetime.now(timezone.utc)
    calls = []

    class FakeClient:
        def list_disputes(self, **kwargs):
            calls.append(("list", kwargs))
            return [
                {
                    "id": "disp_reconciled",
                    "entity": "dispute",
                    "payment_id": "pay_reconciled",
                    "amount": 83000,
                    "currency": "INR",
                    "reason_code": "unauthorised_transaction",
                    "respond_by": int((now + timedelta(days=3)).timestamp()),
                    "status": "open",
                    "phase": "chargeback",
                    "created_at": int(now.timestamp()),
                }
            ]

        def get_payment(self, payment_id, *, expand_card=False):
            calls.append(("payment", payment_id, expand_card))
            return {
                "id": payment_id,
                "order_id": "order_reconciled",
                "method": "card",
                "card": {"network": "Visa"},
            }

    monkeypatch.setattr(
        "api.razorpay_admin.RazorpayClient.from_env",
        lambda: FakeClient(),
    )
    client = TestClient(app)
    request = {
        "merchant_id": "merchant_reconcile",
        "from_timestamp": 100,
        "to_timestamp": 200,
        "count": 50,
    }

    assert client.post("/internal/razorpay/reconcile", json=request).status_code == 401
    response = client.post(
        "/internal/razorpay/reconcile",
        json=request,
        headers={"X-API-Key": "test-api-key"},
    )

    assert response.status_code == 200
    assert response.json()["results"][0]["status"] == "manual_review"
    state = store.get_dispute("disp_reconciled")["state"]
    assert state["provider"] == "razorpay"
    assert state["card_network"] == "VISA"
    assert state["dispute_amount"] == 830.0
    assert calls[0] == (
        "list",
        {"from_timestamp": 100, "to_timestamp": 200, "count": 50, "skip": 0},
    )
    assert calls[1] == ("payment", "pay_reconciled", True)

    duplicate = client.post(
        "/internal/razorpay/reconcile",
        json=request,
        headers={"X-API-Key": "test-api-key"},
    )
    assert duplicate.json()["results"][0]["status"] == "duplicate"


def test_unresolved_events_are_visible_on_protected_endpoint() -> None:
    assert store.claim_provider_event(
        {
            "event_id": "evt_unresolved",
            "provider": "razorpay",
            "event_type": "payment.dispute.created",
            "provider_dispute_id": "disp_unresolved",
            "processing_state": "unresolved",
        }
    )
    client = TestClient(app)

    assert client.get("/internal/razorpay/events").status_code == 401
    response = client.get(
        "/internal/razorpay/events?processing_state=unresolved",
        headers={"X-API-Key": "test-api-key"},
    )

    assert response.status_code == 200
    assert response.json()[0]["provider_dispute_id"] == "disp_unresolved"
