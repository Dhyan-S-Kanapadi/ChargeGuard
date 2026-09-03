from datetime import datetime, timedelta, timezone
from typing import cast

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api.store import store
from api.webhooks import enforce_webhook_rate_limit, reset_webhook_rate_limiter
from core.state import ChargebackState
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


def test_merchants_can_be_listed_for_workspace_selection(client: TestClient) -> None:
    second = {**_merchant_payload(), "merchant_id": "merchant_api_002", "name": "Zulu Store"}
    first = {**_merchant_payload(), "merchant_id": "merchant_api_001", "name": "Alpha Store"}
    assert client.post("/merchants", json=second).status_code == 201
    assert client.post("/merchants", json=first).status_code == 201

    response = client.get("/merchants")

    assert response.status_code == 200
    assert [merchant["merchant_id"] for merchant in response.json()] == [
        "merchant_api_001",
        "merchant_api_002",
    ]


def test_health_reports_model_and_stub_mode(tmp_path, monkeypatch) -> None:
    model_path = tmp_path / "model.pkl"
    train_baseline_model(output_path=model_path, count=200, seed=42)
    monkeypatch.setenv("MODEL_PATH", str(model_path))
    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "true")

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "model_loaded": True,
        "stub_mode": True,
    }


def _stats_state(
    chargeback_id: str,
    *,
    decision: str,
    expected_value: float,
    final_outcome: str | None,
    filed: bool,
    degraded: bool,
) -> ChargebackState:
    return cast(
        ChargebackState,
        {
            "chargeback_id": chargeback_id,
            "decision": decision,
            "expected_value": expected_value,
            "final_outcome": final_outcome,
            "quality_approved": filed,
            "filed_at": datetime.now(timezone.utc) if filed else None,
            "filing_confirmation": f"filed_visa_{chargeback_id}" if filed else None,
            "evidence_collection_degraded": degraded,
        },
    )


def test_stats_returns_correct_seeded_aggregates(client: TestClient) -> None:
    assert store.create_dispute(
        _stats_state(
            "cb_stats_win",
            decision="FIGHT",
            expected_value=100.0,
            final_outcome="WIN",
            filed=True,
            degraded=False,
        )
    )
    assert store.create_dispute(
        _stats_state(
            "cb_stats_accept",
            decision="ACCEPT",
            expected_value=-15.0,
            final_outcome="ACCEPTED_NO_CONTEST",
            filed=False,
            degraded=True,
        )
    )
    assert store.create_dispute(
        _stats_state(
            "cb_stats_escalated",
            decision="ESCALATE_DEGRADED",
            expected_value=0.0,
            final_outcome="PENDING",
            filed=False,
            degraded=True,
        )
    )
    assert store.create_dispute(
        _stats_state(
            "cb_stats_loss",
            decision="FIGHT",
            expected_value=50.0,
            final_outcome="LOSS",
            filed=True,
            degraded=False,
        )
    )

    response = client.get("/stats")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "total_disputes_processed": 4,
        "decisions": {"FIGHT": 2, "ACCEPT": 1, "ESCALATE_DEGRADED": 1},
        "win_rate": 0.5,
        "average_expected_value": 33.75,
        "evidence_collection_degraded_count": 2,
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
    assert detail["third_party_fraud_indicators"] == detail["state"]["third_party_fraud_indicators"]
    assert detail["identity_continuity"] == detail["state"]["identity_continuity"]
    list_response = configured_client.get("/disputes")
    assert list_response.status_code == 200
    assert list_response.json()[0]["chargeback_id"] == "cb_api_001"
    assert list_response.json()[0]["currency"] == "INR"


def test_merchant_support_connector_metadata_is_validated_without_secrets(
    client: TestClient,
) -> None:
    payload = _merchant_payload()
    payload.update(
        {
            "support_connector_ref": "ACME_SUPPORT",
            "gmail_user_id": "support@acme.example",
        }
    )

    response = client.post("/merchants", json=payload)

    assert response.status_code == 201
    merchant = response.json()
    assert merchant["support_connector_ref"] == "ACME_SUPPORT"
    assert merchant["gmail_user_id"] == "support@acme.example"
    assert merchant["freshdesk_domain"] == "demo.freshdesk.com"
    assert "gmail_access_token" not in merchant
    assert "freshdesk_api_key" not in merchant

    with_secrets = _merchant_payload()
    with_secrets["merchant_id"] = "merchant_api_secrets"
    with_secrets["gmail_access_token"] = "must-not-be-stored"
    with_secrets["freshdesk_api_key"] = "must-not-be-stored"
    assert client.post("/merchants", json=with_secrets).status_code == 422

    invalid = _merchant_payload()
    invalid["merchant_id"] = "merchant_api_invalid"
    invalid["support_connector_ref"] = "acme-support"
    assert client.post("/merchants", json=invalid).status_code == 422

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


def test_webhook_degradation_simulation_routes_to_human_review(
    configured_client: TestClient,
) -> None:
    assert configured_client.post("/merchants", json=_merchant_payload()).status_code == 201
    payload = _webhook_payload()
    payload["chargeback_id"] = "cb_api_escalate_001"
    payload["simulate_evidence_degraded"] = True

    response = configured_client.post("/webhook/chargeback", json=payload)

    assert response.status_code == 202
    detail = configured_client.get("/disputes/cb_api_escalate_001").json()
    assert detail["state"]["decision"] == "ESCALATE_DEGRADED"
    assert detail["state"]["degraded_reasons"] == ["demo_simulation"]
    assert detail["state"]["final_outcome"] == "PENDING"


def test_webhook_rate_limit_rejects_thirty_first_request(monkeypatch) -> None:
    monkeypatch.setenv("WEBHOOK_RATE_LIMIT_PER_MINUTE", "30")

    for _ in range(30):
        enforce_webhook_rate_limit("test-api-key")

    with pytest.raises(HTTPException) as raised:
        enforce_webhook_rate_limit("test-api-key")

    assert raised.value.status_code == 429
    assert raised.value.headers["Retry-After"]


def test_webhook_rejects_past_deadline(client: TestClient) -> None:
    payload = _webhook_payload()
    payload["filing_deadline"] = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
    ).isoformat()

    response = client.post("/webhook/chargeback", json=payload)

    assert response.status_code == 422


def test_dispute_ratio_uses_network_volume_and_configured_threshold(
    configured_client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setenv("VISA_DISPUTE_RATIO_THRESHOLD_PCT", "0.1")
    merchant = _merchant_payload()
    merchant["transaction_volume_30d_by_network"] = {"VISA": 1000}
    assert configured_client.post("/merchants", json=merchant).status_code == 201
    assert configured_client.post("/webhook/chargeback", json=_webhook_payload()).status_code == 202

    detail = configured_client.get("/disputes/cb_api_001").json()
    merchant_detail = configured_client.get("/merchants/merchant_api_001").json()
    expected_ratio = {
        "window_days": 30,
        "card_network": "VISA",
        "dispute_count": 1,
        "transaction_count": 1000,
        "current_ratio_pct": 0.1,
        "threshold_pct": 0.1,
        "status": "WARNING",
    }

    assert detail["merchant_dispute_ratio"] == expected_ratio
    assert merchant_detail["merchant_dispute_ratio"]["VISA"] == expected_ratio


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
