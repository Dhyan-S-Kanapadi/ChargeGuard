from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from api.store import store
from main import app
from ml.train import train_baseline_model


@pytest.fixture(autouse=True)
def clear_store() -> None:
    store.clear()
    yield
    store.clear()


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def configured_client(client: TestClient, tmp_path, monkeypatch) -> TestClient:
    model_path = tmp_path / "model.pkl"
    train_baseline_model(output_path=model_path, count=200, seed=42)
    monkeypatch.setenv("MODEL_PATH", str(model_path))
    monkeypatch.setenv("REBUTTAL_OUTPUT_DIR", str(tmp_path / "rebuttals"))
    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "true")
    monkeypatch.setenv("TRAINING_DATA_PATH", str(tmp_path / "outcomes.json"))
    monkeypatch.setenv("TRAINING_METADATA_PATH", str(tmp_path / "metadata.json"))
    monkeypatch.setenv("PLAYBOOK_STATS_PATH", str(tmp_path / "stats.json"))
    monkeypatch.setenv("RETRAIN_RECORD_THRESHOLD", "10")
    return client


def _merchant_payload() -> dict:
    return {
        "merchant_id": "merchant_api_001",
        "name": "API Demo Merchant",
        "vertical": "ecommerce",
        "payment_provider": "razorpay",
        "shipping_provider": "shiprocket",
        "razorpay_key": "secret_key_not_returned",
        "shiprocket_key": "secret_shipping_key_not_returned",
        "freshdesk_domain": "demo.freshdesk.com",
        "average_order_value": 1800.0,
        "chargeback_history_count": 4,
    }


def _webhook_payload() -> dict:
    return {
        "chargeback_id": "cb_api_001",
        "reason_code": "10.4",
        "card_network": "VISA",
        "dispute_amount": 2500.0,
        "currency": "inr",
        "filing_deadline": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        "merchant_id": "merchant_api_001",
        "order_id": "order_api_001",
        "payment_id": "pay_api_001",
        "tracking_id": "tracking_api_001",
        "card_fingerprint": "card_fingerprint_001",
    }


def test_webhook_runs_graph_and_exposes_completed_dispute(configured_client: TestClient) -> None:
    merchant_response = configured_client.post("/merchants", json=_merchant_payload())

    assert merchant_response.status_code == 201
    assert "razorpay_key" not in merchant_response.json()
    assert "shiprocket_key" not in merchant_response.json()

    webhook_response = configured_client.post(
        "/webhook/chargeback",
        json=_webhook_payload(),
    )

    assert webhook_response.status_code == 202
    assert webhook_response.json() == {
        "status": "received",
        "chargeback_id": "cb_api_001",
    }

    detail_response = configured_client.get("/disputes/cb_api_001")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["status"] == "completed"
    assert detail["state"]["decision"] == "FIGHT"
    assert detail["state"]["quality_approved"] is True
    assert detail["state"]["rebuttal_document_path"].endswith(".pdf")
    assert detail["state"]["filing_confirmation"].startswith("filed_visa_cb_api_001_")
    assert detail["state"]["final_outcome"] is None
    assert detail["state"]["outcome_recorded_at"] is None

    list_response = configured_client.get("/disputes")
    assert list_response.status_code == 200
    assert list_response.json()[0]["chargeback_id"] == "cb_api_001"
    assert list_response.json()[0]["currency"] == "INR"


def test_webhook_rejects_unknown_merchant(client: TestClient) -> None:
    response = client.post("/webhook/chargeback", json=_webhook_payload())

    assert response.status_code == 404
    assert response.json()["detail"] == "Merchant not found."


def test_webhook_rejects_duplicate_chargeback(configured_client: TestClient) -> None:
    assert configured_client.post("/merchants", json=_merchant_payload()).status_code == 201
    assert configured_client.post("/webhook/chargeback", json=_webhook_payload()).status_code == 202

    duplicate = configured_client.post("/webhook/chargeback", json=_webhook_payload())

    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Chargeback already exists."


def test_webhook_rejects_past_deadline(client: TestClient) -> None:
    payload = _webhook_payload()
    payload["filing_deadline"] = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
    ).isoformat()

    response = client.post("/webhook/chargeback", json=payload)

    assert response.status_code == 422


def test_outcome_endpoint_records_terminal_feedback(
    configured_client: TestClient,
    tmp_path,
) -> None:
    assert configured_client.post("/merchants", json=_merchant_payload()).status_code == 201
    assert configured_client.post("/webhook/chargeback", json=_webhook_payload()).status_code == 202

    outcome_response = configured_client.post(
        "/disputes/cb_api_001/outcome",
        json={"outcome": "WIN", "reason": "Issuer accepted delivery evidence."},
    )

    assert outcome_response.status_code == 200
    assert outcome_response.json()["final_outcome"] == "WIN"
    assert outcome_response.json()["outcome_reason"] == "Issuer accepted delivery evidence."
    assert (tmp_path / "outcomes.json").is_file()

    duplicate = configured_client.post(
        "/disputes/cb_api_001/outcome",
        json={"outcome": "LOSS"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Final outcome already recorded."
