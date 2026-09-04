import hashlib
import hmac
from collections import Counter
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from api import razorpay_simulator
from api.store import store
from integrations.razorpay_webhook import verify_signature
from main import app
from ml.train import train_baseline_model


def _merchant():
    return {"merchant_id": "merchant_sim", "name": "Simulator Merchant", "vertical": "ecommerce", "payment_provider": "razorpay", "razorpay_account_id": "acc_SIM", "freshdesk_domain": "", "average_order_value": 1.0, "chargeback_history_count": 0}


def _payload():
    return {"merchant_id": "merchant_sim", "payment_id": "pay_sim", "order_id": "order_sim", "payment_amount_paise": 250000, "dispute_amount_paise": 250000, "currency": "INR", "method": "upi", "card_network": "VISA", "network_reason_code": "10.4", "razorpay_reason_code": "unauthorised_transaction"}


def test_simulator_is_disabled_by_default(monkeypatch) -> None:
    store.clear(); monkeypatch.setenv("API_KEY", "key"); monkeypatch.delenv("RAZORPAY_SIMULATOR_ENABLED", raising=False); monkeypatch.delenv("CHARGEBACK_SIMULATOR_ENABLED", raising=False)
    assert TestClient(app).get("/dev/razorpay-simulator/disputes", headers={"X-API-Key": "key"}).status_code == 404


def test_simulator_is_disabled_in_production_even_when_enabled(monkeypatch) -> None:
    store.clear(); monkeypatch.setenv("API_KEY", "key"); monkeypatch.setenv("RAZORPAY_SIMULATOR_ENABLED", "true"); monkeypatch.setenv("ENVIRONMENT", "production")
    assert TestClient(app).get("/dev/razorpay-simulator/disputes", headers={"X-API-Key": "key"}).status_code == 404


def test_simulator_create_and_lifecycle(monkeypatch) -> None:
    store.clear(); assert store.create_merchant(_merchant())
    monkeypatch.setenv("API_KEY", "key"); monkeypatch.setenv("RAZORPAY_SIMULATOR_ENABLED", "true"); monkeypatch.setenv("ENVIRONMENT", "development"); monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "secret")
    monkeypatch.setattr(razorpay_simulator, "_deliver", lambda record, event, state: {"event_id": "evt_" + state, "event_name": event, "delivery": {"status_code": 202}, "payload_sha256": "hash"})
    monkeypatch.setattr(razorpay_simulator, "_await_provider_event", lambda event_id: None)
    client = TestClient(app, headers={"X-API-Key": "key"})
    created = client.post("/dev/razorpay-simulator/disputes", json=_payload()).json()
    dispute_id = created["dispute_id"]
    assert dispute_id.startswith("disp_SIM_")
    assert created["order_seeded"] is True
    order = store.get_order_by_provider_payment_id("merchant_sim", "pay_sim")
    assert order is not None
    assert order["provider_order_id"] == "order_sim"
    assert order["tracking_id"].startswith("trk_SIM_")
    assert client.post(f"/dev/razorpay-simulator/disputes/{dispute_id}/transition", json={"state": "won"}).status_code == 409
    assert client.post(f"/dev/razorpay-simulator/disputes/{dispute_id}/transition", json={"state": "under_review"}).status_code == 200
    assert client.post(f"/dev/razorpay-simulator/disputes/{dispute_id}/transition", json={"state": "won"}).status_code == 200


def test_simulator_delivery_signs_exact_body_with_mock_transport() -> None:
    seen = {}
    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content; seen["signature"] = request.headers["X-Razorpay-Signature"]
        return httpx.Response(202, text="accepted")
    body = b'{"entity":"event"}'
    result = razorpay_simulator.deliver_simulator_event("http://127.0.0.1:8000/webhook/razorpay", body, "evt_SIM_x", "secret", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert result["status_code"] == 202
    assert verify_signature(seen["body"], seen["signature"], "secret")


def test_simulator_refuses_non_loopback_delivery() -> None:
    with pytest.raises(ValueError, match="loopback"):
        razorpay_simulator.deliver_simulator_event(
            "https://api.razorpay.com/webhook/razorpay",
            b"{}",
            "evt_SIM_x",
            "secret",
        )


def test_upi_simulator_payload_has_no_card_entity() -> None:
    record = {
        **_payload(),
        "account_id": "acc_SIM",
        "dispute_id": "disp_SIM_upi",
        "state": "open",
        "created_at": datetime.now(timezone.utc),
        "respond_by": datetime.now(timezone.utc) + timedelta(days=3),
    }
    envelope = razorpay_simulator.build_simulator_envelope(
        record,
        "payment.dispute.created",
        "open",
    )
    payment = envelope["payload"]["payment"]["entity"]
    assert payment["method"] == "upi"
    assert "card" not in payment


def test_manual_simulator_rejects_reused_payment_or_order_ids(monkeypatch) -> None:
    store.clear()
    assert store.create_merchant(_merchant())
    monkeypatch.setenv("API_KEY", "key")
    monkeypatch.setenv("RAZORPAY_SIMULATOR_ENABLED", "true")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "secret")
    monkeypatch.setattr(
        razorpay_simulator,
        "_deliver",
        lambda record, event, state: {
            "event_id": "evt_" + state,
            "event_name": event,
            "delivery": {"status_code": 202},
            "payload_sha256": "hash",
        },
    )
    client = TestClient(app, headers={"X-API-Key": "key"})

    assert client.post("/dev/razorpay-simulator/disputes", json=_payload()).status_code == 200
    response = client.post("/dev/razorpay-simulator/disputes", json=_payload())

    assert response.status_code == 409
    assert "already used" in response.json()["detail"]


def test_scenario_catalog_has_four_examples_per_family(monkeypatch) -> None:
    store.clear()
    monkeypatch.setenv("API_KEY", "key")
    monkeypatch.setenv("RAZORPAY_SIMULATOR_ENABLED", "true")
    monkeypatch.setenv("ENVIRONMENT", "development")

    response = TestClient(app).get(
        "/dev/razorpay-simulator/scenarios",
        headers={"X-API-Key": "key"},
    )

    assert response.status_code == 200
    scenarios = response.json()
    assert len(scenarios) == 24
    assert len({scenario["id"] for scenario in scenarios}) == 24
    assert Counter(scenario["family"] for scenario in scenarios) == {
        "Decision routing": 4,
        "Network playbooks": 4,
        "Payment rails": 4,
        "Webhook trust": 4,
        "Provider lifecycle": 4,
        "Automation boundaries": 4,
    }


def test_catalog_scenario_run_uses_unique_ids_and_seeds_order(monkeypatch) -> None:
    store.clear()
    assert store.create_merchant(_merchant())
    monkeypatch.setenv("API_KEY", "key")
    monkeypatch.setenv("RAZORPAY_SIMULATOR_ENABLED", "true")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "secret")
    monkeypatch.setattr(
        razorpay_simulator,
        "_deliver",
        lambda record, event, state: {
            "event_id": f"evt_{record['dispute_id']}_{state}",
            "event_name": event,
            "delivery": {"status_code": 202},
            "payload_sha256": "hash",
        },
    )
    client = TestClient(app, headers={"X-API-Key": "key"})

    first = client.post(
        "/dev/razorpay-simulator/scenarios/friendly-fraud-high-value/run",
        json={"merchant_id": "merchant_sim"},
    ).json()
    second = client.post(
        "/dev/razorpay-simulator/scenarios/friendly-fraud-high-value/run",
        json={"merchant_id": "merchant_sim"},
    ).json()

    assert first["dispute_id"] != second["dispute_id"]
    assert first["order_seeded"] is True
    record = store.get_simulator_dispute(first["dispute_id"])
    assert record is not None
    assert record["scenario_id"] == "friendly-fraud-high-value"
    assert store.get_order_by_provider_payment_id(
        "merchant_sim", record["payment_id"]
    ) is not None


@pytest.mark.parametrize(
    ("scenario_id", "expected_deliveries", "expected_state"),
    [
        ("webhook-invalid-signature", 1, "signature_rejected"),
        ("webhook-duplicate-event", 2, "open"),
        ("webhook-unknown-account", 1, "open"),
        ("lifecycle-under-review", 2, "under_review"),
        ("lifecycle-won-before-created", 1, "won"),
        ("lifecycle-closed", 2, "closed"),
    ],
)
def test_catalog_special_delivery_behaviors(
    monkeypatch,
    scenario_id: str,
    expected_deliveries: int,
    expected_state: str,
) -> None:
    store.clear()
    assert store.create_merchant(_merchant())
    monkeypatch.setenv("API_KEY", "key")
    monkeypatch.setenv("RAZORPAY_SIMULATOR_ENABLED", "true")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "secret")
    monkeypatch.setattr(
        razorpay_simulator,
        "_deliver",
        lambda record, event, state: {
            "event_id": "evt_" + state,
            "event_name": event,
            "delivery": {"status_code": 202},
            "payload_sha256": "hash",
        },
    )
    monkeypatch.setattr(
        razorpay_simulator,
        "_deliver_prepared",
        lambda event_id, body, webhook_secret=None: {
            "event_id": event_id,
            "delivery": {
                "status_code": 401 if webhook_secret and webhook_secret.endswith("-invalid") else 202
            },
            "payload_sha256": hashlib.sha256(body).hexdigest(),
        },
    )
    monkeypatch.setattr(
        razorpay_simulator,
        "_await_provider_event",
        lambda event_id: None,
    )
    response = TestClient(app, headers={"X-API-Key": "key"}).post(
        f"/dev/razorpay-simulator/scenarios/{scenario_id}/run",
        json={"merchant_id": "merchant_sim"},
    )

    assert response.status_code == 200
    result = response.json()
    assert len(result["deliveries"]) == expected_deliveries
    record = store.get_simulator_dispute(result["dispute_id"])
    assert record is not None
    assert record["state"] == expected_state
    if scenario_id == "webhook-duplicate-event":
        assert result["deliveries"][0]["event_id"] == result["deliveries"][1]["event_id"]
    if scenario_id == "webhook-unknown-account":
        assert record["account_id"].startswith("acc_SIM_UNKNOWN_")


def test_simulator_create_reaches_completed_graph_with_exact_order_correlation(
    monkeypatch,
    tmp_path,
) -> None:
    store.clear()
    assert store.create_merchant(_merchant())
    model_path = tmp_path / "model.pkl"
    train_baseline_model(output_path=model_path, count=200, seed=42)
    monkeypatch.setenv("API_KEY", "key")
    monkeypatch.setenv("RAZORPAY_SIMULATOR_ENABLED", "true")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "secret")
    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "true")
    monkeypatch.setenv("MODEL_PATH", str(model_path))
    monkeypatch.setenv("REBUTTAL_OUTPUT_DIR", str(tmp_path / "rebuttals"))
    monkeypatch.setenv("DECISION_REVIEW_ENABLED", "false")
    webhook_client = TestClient(app)

    def deliver_locally(
        target_url: str,
        body: bytes,
        event_id: str,
        webhook_secret: str,
        **_kwargs,
    ) -> dict:
        assert target_url == "http://127.0.0.1:8000/webhook/razorpay"
        signature = hmac.new(
            webhook_secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        response = webhook_client.post(
            "/webhook/razorpay",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Razorpay-Signature": signature,
                "x-razorpay-event-id": event_id,
            },
        )
        return {
            "status_code": response.status_code,
            "body": response.text,
            "signature": signature,
        }

    monkeypatch.setattr(
        razorpay_simulator,
        "deliver_simulator_event",
        deliver_locally,
    )
    payload = {
        **_payload(),
        "method": "card",
        "card_network": "VISA",
        "network_reason_code": "13.1",
        "razorpay_reason_code": "product_not_received",
    }

    response = TestClient(app, headers={"X-API-Key": "key"}).post(
        "/dev/razorpay-simulator/disputes",
        json=payload,
    )

    assert response.status_code == 200
    result = response.json()
    assert result["delivery"]["status_code"] == 202
    dispute = store.get_dispute(result["dispute_id"])
    assert dispute is not None
    assert dispute["status"] == "completed"
    assert dispute["state"]["commerce_order_id"] == "order_sim"
    assert dispute["state"]["tracking_id"].startswith("trk_SIM_")
    assert "commerce_order_correlation_unavailable" not in dispute["state"]["degraded_reasons"]
    assert dispute["state"]["decision"] == "FIGHT"
    assert dispute["state"]["quality_approved"] is True
    assert dispute["state"]["filing_confirmation"].startswith("filed_")
