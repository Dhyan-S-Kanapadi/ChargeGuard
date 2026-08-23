from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from api.store import store
from api.webhooks import reset_webhook_rate_limiter
from main import app
from ml.train import train_baseline_model


@pytest.fixture(autouse=True)
def clear_store() -> None:
    store.clear()
    reset_webhook_rate_limiter()
    yield
    store.clear()
    reset_webhook_rate_limiter()


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setenv("API_KEY", "test-api-key")
    return TestClient(app, headers={"X-API-Key": "test-api-key"})


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


def _contains_key(value, blocked_key: str) -> bool:
    if isinstance(value, dict):
        return blocked_key in value or any(_contains_key(item, blocked_key) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, blocked_key) for item in value)
    return False


def test_api_key_is_required(monkeypatch) -> None:
    monkeypatch.setenv("API_KEY", "test-api-key")
    unauthenticated_client = TestClient(app)

    response = unauthenticated_client.get("/disputes")
    authenticated_response = unauthenticated_client.get(
        "/disputes",
        headers={"X-API-Key": "test-api-key"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing or invalid API key."
    assert authenticated_response.status_code == 200


def test_stats_returns_live_dispute_aggregates(configured_client: TestClient) -> None:
    assert configured_client.post("/merchants", json=_merchant_payload()).status_code == 201
    assert configured_client.post("/webhook/chargeback", json=_webhook_payload()).status_code == 202

    response = configured_client.get("/stats")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_disputes_processed"] == 1
    assert set(payload["decisions"]) == {"FIGHT", "ACCEPT", "ESCALATE_DEGRADED"}
    assert "win_rate" in payload
    assert "average_expected_value" in payload
    assert "evidence_collection_degraded_count" in payload


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


def test_dispute_detail_redacts_raw_payloads_and_transaction_pii(
    configured_client: TestClient,
) -> None:
    assert configured_client.post("/merchants", json=_merchant_payload()).status_code == 201
    assert configured_client.post("/webhook/chargeback", json=_webhook_payload()).status_code == 202

    detail_response = configured_client.get("/disputes/cb_api_001")

    assert detail_response.status_code == 200
    payload = detail_response.json()
    transaction = payload["state"]["transaction"]
    assert transaction is not None
    assert "customer_email" not in transaction
    assert "ip_address" not in transaction
    assert "device_id" not in transaction
    assert not _contains_key(payload, "raw")


def test_dispute_detail_allows_raw_payloads_with_internal_token(
    configured_client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setenv("INTERNAL_API_TOKEN", "debug-token")
    assert configured_client.post("/merchants", json=_merchant_payload()).status_code == 201
    assert configured_client.post("/webhook/chargeback", json=_webhook_payload()).status_code == 202

    forbidden = configured_client.get("/disputes/cb_api_001?include_raw=true")
    detail_response = configured_client.get(
        "/disputes/cb_api_001?include_raw=true",
        headers={"X-Internal-Token": "debug-token"},
    )

    assert forbidden.status_code == 403
    assert detail_response.status_code == 200
    payload = detail_response.json()
    transaction = payload["state"]["transaction"]
    assert transaction["customer_email"]
    assert transaction["ip_address"]
    assert transaction["device_id"]
    assert _contains_key(payload, "raw")


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


def test_webhook_rate_limit_rejects_thirty_first_request(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setenv("WEBHOOK_RATE_LIMIT_PER_MINUTE", "30")

    for _ in range(30):
        response = client.post("/webhook/chargeback", json=_webhook_payload())
        assert response.status_code == 404

    limited = client.post("/webhook/chargeback", json=_webhook_payload())

    assert limited.status_code == 429
    assert limited.headers["Retry-After"]


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


def test_outcome_endpoint_rejects_accept_path_disputes(
    configured_client: TestClient,
    tmp_path,
) -> None:
    assert configured_client.post("/merchants", json=_merchant_payload()).status_code == 201
    payload = _webhook_payload()
    payload["chargeback_id"] = "cb_api_accept_001"
    payload["dispute_amount"] = 10.0
    payload["currency"] = "USD"

    response = configured_client.post("/webhook/chargeback", json=payload)
    assert response.status_code == 202

    detail = configured_client.get("/disputes/cb_api_accept_001").json()
    if detail["state"]["decision"] != "ACCEPT":
        # Force the API guard directly if the trained model fights this fixture.
        state = detail["state"]
        state["decision"] = "ACCEPT"
        state["quality_approved"] = False
        state["filing_confirmation"] = "accepted_no_filing"
        state["filed_at"] = None
        state["final_outcome"] = "ACCEPTED_NO_CONTEST"
        store.update_dispute("cb_api_accept_001", status="completed", state=state)

    outcome_response = configured_client.post(
        "/disputes/cb_api_accept_001/outcome",
        json={"outcome": "WIN"},
    )

    assert outcome_response.status_code == 409
    assert outcome_response.json()["detail"] == (
        "Only filed representment disputes can record adjudicated outcomes."
    )
