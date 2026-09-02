from datetime import datetime, timedelta, timezone
import json

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


def test_failed_reconciliation_event_retries_from_sanitized_event_data(
    monkeypatch,
) -> None:
    assert store.create_merchant(_merchant())
    now = datetime.now(timezone.utc)

    class FakeClient:
        def list_disputes(self, **kwargs):
            return [
                {
                    "id": "disp_reconcile_retry",
                    "payment_id": "pay_reconcile_retry",
                    "amount": 83000,
                    "currency": "INR",
                    "reason_code": "unauthorised_transaction",
                    "respond_by": int((now + timedelta(days=3)).timestamp()),
                    "status": "open",
                    "phase": "chargeback",
                    "created_at": int(now.timestamp()),
                    "updated_at": int(now.timestamp()),
                }
            ]

        def get_payment(self, payment_id, *, expand_card=False):
            return {
                "id": payment_id,
                "order_id": "order_reconcile_retry",
                "method": "card",
                "email": "customer@example.test",
                "contact": "+919999999999",
                "vpa": "customer@upi",
                "notes": {"private_note": "do-not-store"},
                "card": {"network": "Visa", "last4": "1111"},
            }

    monkeypatch.setattr(
        "api.razorpay_admin.RazorpayClient.from_env",
        lambda: FakeClient(),
    )
    monkeypatch.setattr(
        "api.razorpay_admin.normalize_dispute",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("temporary")),
    )
    client = TestClient(app)
    headers = {"X-API-Key": "test-api-key"}
    request = {"merchant_id": "merchant_reconcile", "count": 25}

    failed = client.post("/internal/razorpay/reconcile", json=request, headers=headers)
    assert failed.status_code == 200
    assert failed.json()["results"][0]["status"] == "failed"

    event = store.list_provider_events()[0]
    event_id = event["event_id"]
    serialized = json.dumps(event["event_data"])
    assert event["event_id_source"] == "reconciliation"
    assert event["attempt_count"] == 1
    assert "customer@example.test" not in serialized
    assert "+919999999999" not in serialized
    assert "customer@upi" not in serialized
    assert "do-not-store" not in serialized
    assert "last4" not in serialized

    public_events = client.get("/internal/razorpay/events", headers=headers)
    assert public_events.status_code == 200
    assert "event_data" not in public_events.json()[0]
    assert client.post(f"/internal/razorpay/events/{event_id}/retry").status_code == 401

    retry = client.post(
        f"/internal/razorpay/events/{event_id}/retry",
        headers=headers,
    )
    assert retry.status_code == 200
    assert store.get_provider_event(event_id)["processing_state"] == "manual_review"
    assert store.get_provider_event(event_id)["attempt_count"] == 2
    assert store.get_dispute("disp_reconcile_retry") is not None
    assert client.post(
        f"/internal/razorpay/events/{event_id}/retry",
        headers=headers,
    ).status_code == 409

    duplicate = client.post("/internal/razorpay/reconcile", json=request, headers=headers)
    assert duplicate.json()["results"][0]["status"] == "duplicate"
    assert len(store.list_disputes()) == 1


def test_process_pending_recovers_failed_reconciliation_event(monkeypatch) -> None:
    assert store.create_merchant(_merchant())
    now = datetime.now(timezone.utc)

    class FakeClient:
        def list_disputes(self, **kwargs):
            return [
                {
                    "id": "disp_reconcile_pending",
                    "payment_id": "pay_reconcile_pending",
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
            return {
                "id": payment_id,
                "order_id": "order_reconcile_pending",
                "method": "card",
                "card": {"network": "Visa"},
            }

    monkeypatch.setattr(
        "api.razorpay_admin.RazorpayClient.from_env",
        lambda: FakeClient(),
    )
    monkeypatch.setattr(
        "api.razorpay_admin.normalize_dispute",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("temporary")),
    )
    client = TestClient(app)
    headers = {"X-API-Key": "test-api-key"}
    request = {"merchant_id": "merchant_reconcile", "count": 25}
    assert client.post(
        "/internal/razorpay/reconcile", json=request, headers=headers
    ).json()["results"][0]["status"] == "failed"

    recovery = client.post(
        "/internal/razorpay/process-pending?limit=25",
        headers=headers,
    )

    assert recovery.status_code == 200
    assert recovery.json()["scheduled"] == 1
    event = store.list_provider_events()[0]
    assert event["processing_state"] == "manual_review"
    assert event["attempt_count"] == 2
    assert store.get_dispute("disp_reconcile_pending") is not None


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
