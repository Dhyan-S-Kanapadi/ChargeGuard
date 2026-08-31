from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from api import webhooks
from api.store import InMemoryStore, store
from core.state import MerchantProfile
from main import app


SECRET = "webhook-test-secret"


@pytest.fixture(autouse=True)
def reset_store(monkeypatch):
    store.clear()
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("API_KEY", "test-api-key")
    yield
    store.clear()


def _merchant(account_id: str = "acc_test") -> MerchantProfile:
    return {
        "merchant_id": "merchant_rzp",
        "name": "Razorpay Merchant",
        "vertical": "ecommerce",
        "payment_provider": "razorpay",
        "razorpay_account_id": account_id,
        "freshdesk_domain": "",
        "average_order_value": 1000.0,
        "chargeback_history_count": 0,
    }


def _body(*, account_id="acc_test", event="payment.dispute.created", event_id="evt_1", mapped=True):
    now = datetime.now(timezone.utc)
    notes = {
        "chargeguard_card_network": "VISA",
        "chargeguard_network_reason_code": "10.4",
    } if mapped else {}
    payload = {
        "entity": "event", "account_id": account_id, "event": event,
        "payload": {"payment": {"entity": {"id": "pay_1", "amount": 250000, "currency": "INR", "order_id": "order_1", "notes": notes}},
                    "dispute": {"entity": {"id": "disp_1", "payment_id": "pay_1", "amount": 250000, "currency": "INR", "reason_code": "unauthorised_transaction", "respond_by": int((now + timedelta(days=5)).timestamp()), "status": "open", "phase": "chargeback"}}},
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"X-Razorpay-Signature": signature, "x-razorpay-event-id": event_id}


def test_valid_signature_maps_paise_and_runs_once(monkeypatch) -> None:
    assert store.create_merchant(_merchant())
    calls = []
    monkeypatch.setattr(webhooks, "run_chargeback_graph", lambda state: calls.append(state["chargeback_id"]))
    raw, headers = _body()

    response = TestClient(app).post("/webhook/razorpay", content=raw, headers=headers)

    assert response.status_code == 202
    assert calls == ["disp_1"]
    state = store.get_dispute("disp_1")["state"]
    assert state["dispute_amount"] == 2500.0
    assert state["provider_reason_code"] == "unauthorised_transaction"
    assert state["reason_code"] == "10.4"
    assert state["provider_respond_by"].tzinfo is not None


def test_invalid_or_missing_signature_is_rejected() -> None:
    raw, headers = _body()
    client = TestClient(app)
    assert client.post("/webhook/razorpay", content=raw, headers={"x-razorpay-event-id": "evt_x"}).status_code == 401
    headers["X-Razorpay-Signature"] = "bad"
    assert client.post("/webhook/razorpay", content=raw, headers=headers).status_code == 401
    assert client.post("/webhook/razorpay", content=raw, headers={"X-Razorpay-Signature": hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()}).status_code == 400


def test_signature_requires_exact_raw_bytes() -> None:
    assert store.create_merchant(_merchant())
    raw, headers = _body()
    changed = raw + b" "
    assert TestClient(app).post("/webhook/razorpay", content=changed, headers=headers).status_code == 401


def test_unknown_and_unmapped_events_do_not_create_disputes() -> None:
    raw, headers = _body(account_id="acc_unknown")
    assert TestClient(app).post("/webhook/razorpay", content=raw, headers=headers).status_code == 404
    assert store.get_dispute("disp_1") is None


def test_unsupported_authentic_event_is_safely_acknowledged() -> None:
    raw, headers = _body(event="payment.dispute.some_future_status", event_id="evt_future")
    response = TestClient(app).post("/webhook/razorpay", content=raw, headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert store.create_merchant(_merchant())
    raw, headers = _body(event_id="evt_unmapped", mapped=False)
    response = TestClient(app).post("/webhook/razorpay", content=raw, headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "unmapped"
    assert store.get_dispute("disp_1") is None


def test_duplicate_event_is_acknowledged_without_a_second_graph_run(monkeypatch) -> None:
    assert store.create_merchant(_merchant())
    calls = []
    monkeypatch.setattr(webhooks, "run_chargeback_graph", lambda state: calls.append(state["chargeback_id"]))
    raw, headers = _body()
    client = TestClient(app)
    assert client.post("/webhook/razorpay", content=raw, headers=headers).status_code == 202
    duplicate = client.post("/webhook/razorpay", content=raw, headers=headers)
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "duplicate"
    assert calls == ["disp_1"]


def test_duplicate_razorpay_account_and_provider_events_persist(tmp_path) -> None:
    path = tmp_path / "store.json"
    local = InMemoryStore(path=path)
    assert local.create_merchant(_merchant())
    duplicate = _merchant()
    duplicate["merchant_id"] = "other"
    assert local.create_merchant(duplicate) is False
    assert local.claim_provider_event({"provider_event_id": "evt_saved", "provider": "razorpay", "event_name": "payment.dispute.created", "account_id": "acc_test", "chargeback_id": "disp_saved", "payment_id": "pay_saved", "payload_sha256": "hash"})
    reloaded = InMemoryStore(path=path)
    assert reloaded.get_merchant_by_razorpay_account_id("acc_test")["merchant_id"] == "merchant_rzp"
    assert reloaded.get_provider_event("evt_saved") is not None
