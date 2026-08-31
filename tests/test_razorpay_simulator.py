import hashlib
import hmac

import httpx
from fastapi.testclient import TestClient

from api import razorpay_simulator
from api.store import store
from integrations.razorpay_webhook import verify_signature
from main import app


def _merchant():
    return {"merchant_id": "merchant_sim", "name": "Simulator Merchant", "vertical": "ecommerce", "payment_provider": "razorpay", "razorpay_account_id": "acc_SIM", "freshdesk_domain": "", "average_order_value": 1.0, "chargeback_history_count": 0}


def _payload():
    return {"merchant_id": "merchant_sim", "payment_id": "pay_sim", "order_id": "order_sim", "payment_amount_paise": 250000, "dispute_amount_paise": 250000, "currency": "INR", "method": "upi", "card_network": "VISA", "network_reason_code": "10.4", "razorpay_reason_code": "unauthorised_transaction"}


def test_simulator_is_disabled_by_default(monkeypatch) -> None:
    store.clear(); monkeypatch.setenv("API_KEY", "key")
    assert TestClient(app).get("/dev/razorpay-simulator/disputes", headers={"X-API-Key": "key"}).status_code == 404


def test_simulator_create_and_lifecycle(monkeypatch) -> None:
    store.clear(); assert store.create_merchant(_merchant())
    monkeypatch.setenv("API_KEY", "key"); monkeypatch.setenv("CHARGEBACK_SIMULATOR_ENABLED", "true"); monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "secret")
    monkeypatch.setattr(razorpay_simulator, "_deliver", lambda record, event, state: {"event_id": "evt_" + state, "event_name": event, "delivery": {"status_code": 202}, "payload_sha256": "hash"})
    client = TestClient(app, headers={"X-API-Key": "key"})
    created = client.post("/dev/razorpay-simulator/disputes", json=_payload()).json()
    dispute_id = created["dispute_id"]
    assert dispute_id.startswith("disp_SIM_")
    assert client.post(f"/dev/razorpay-simulator/disputes/{dispute_id}/transition", json={"state": "won"}).status_code == 409
    assert client.post(f"/dev/razorpay-simulator/disputes/{dispute_id}/transition", json={"state": "under_review"}).status_code == 200
    assert client.post(f"/dev/razorpay-simulator/disputes/{dispute_id}/transition", json={"state": "won"}).status_code == 200


def test_simulator_delivery_signs_exact_body_with_mock_transport() -> None:
    seen = {}
    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content; seen["signature"] = request.headers["X-Razorpay-Signature"]
        return httpx.Response(202, text="accepted")
    body = b'{"entity":"event"}'
    result = razorpay_simulator.deliver_simulator_event("https://local.test/webhook", body, "evt_SIM_x", "secret", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert result["status_code"] == 202
    assert verify_signature(seen["body"], seen["signature"], "secret")
