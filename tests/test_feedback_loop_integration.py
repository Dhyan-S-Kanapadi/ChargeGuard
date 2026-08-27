import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from api.store import store
from api.webhooks import reset_webhook_rate_limiter
from main import app
from ml.train import train_baseline_model


@pytest.fixture
def feedback_client(tmp_path, monkeypatch) -> TestClient:
    store.clear()
    reset_webhook_rate_limiter()
    model_path = tmp_path / "model.pkl"
    train_baseline_model(output_path=model_path, count=200, seed=42)
    monkeypatch.setenv("API_KEY", "feedback-test-key")
    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "true")
    monkeypatch.setenv("MODEL_PATH", str(model_path))
    monkeypatch.setenv("REBUTTAL_OUTPUT_DIR", str(tmp_path / "rebuttals"))
    monkeypatch.setenv("TRAINING_DATA_PATH", str(tmp_path / "outcomes.json"))
    monkeypatch.setenv("TRAINING_METADATA_PATH", str(tmp_path / "training_metadata.json"))
    monkeypatch.setenv("PLAYBOOK_STATS_PATH", str(tmp_path / "playbook_stats.json"))
    monkeypatch.setenv("RETRAIN_RECORD_THRESHOLD", "3")
    monkeypatch.setenv("SYNTHETIC_SEED_COUNT", "200")
    monkeypatch.setenv("SYNTHETIC_DECAY_PER_REAL_RECORD", "4")
    client = TestClient(app, headers={"X-API-Key": "feedback-test-key"})
    yield client
    store.clear()
    reset_webhook_rate_limiter()


def _merchant_payload() -> dict:
    return {
        "merchant_id": "feedback_merchant",
        "name": "Feedback Test Merchant",
        "vertical": "ecommerce",
        "payment_provider": "stripe",
        "shipping_provider": "shiprocket",
        "freshdesk_domain": "feedback.example.test",
        "average_order_value": 500.0,
        "chargeback_history_count": 0,
    }


def _webhook_payload(chargeback_id: str, amount: float) -> dict:
    return {
        "chargeback_id": chargeback_id,
        "reason_code": "10.4",
        "card_network": "VISA",
        "dispute_amount": amount,
        "currency": "USD",
        "filing_deadline": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        "merchant_id": "feedback_merchant",
        "order_id": f"order_{chargeback_id}",
        "payment_id": f"payment_{chargeback_id}",
        "tracking_id": f"tracking_{chargeback_id}",
        "card_fingerprint": f"card_{chargeback_id}",
    }


def test_feedback_loop_retrains_with_decayed_synthetic_data(
    feedback_client: TestClient,
    tmp_path,
) -> None:
    assert feedback_client.post("/merchants", json=_merchant_payload()).status_code == 201

    for index, outcome in enumerate(("WIN", "LOSS", "WIN"), start=1):
        chargeback_id = f"cb_feedback_{index}"
        assert feedback_client.post(
            "/webhook/chargeback",
            json=_webhook_payload(chargeback_id, 1_000.0),
        ).status_code == 202
        dispute = feedback_client.get(f"/disputes/{chargeback_id}").json()
        assert dispute["state"]["decision"] == "FIGHT"

        response = feedback_client.post(
            f"/disputes/{chargeback_id}/outcome",
            json={"outcome": outcome, "reason": f"Integration record {index}"},
        )
        assert response.status_code == 200

    metadata = json.loads((tmp_path / "training_metadata.json").read_text(encoding="utf-8"))
    assert metadata["last_trained_record_count"] == 3
    assert metadata["training_split"]["synthetic_record_count"] == 188
    assert metadata["training_split"]["real_record_count"] == 3

    outcomes_path = tmp_path / "outcomes.json"
    assert len(json.loads(outcomes_path.read_text(encoding="utf-8"))) == 3

    accept_id = "cb_feedback_accept"
    assert feedback_client.post(
        "/webhook/chargeback",
        json=_webhook_payload(accept_id, 1.0),
    ).status_code == 202
    accept_dispute = feedback_client.get(f"/disputes/{accept_id}").json()
    assert accept_dispute["state"]["decision"] == "ACCEPT"

    rejected_outcome = feedback_client.post(
        f"/disputes/{accept_id}/outcome",
        json={"outcome": "WIN", "reason": "Must not enter feedback."},
    )
    assert rejected_outcome.status_code == 409
    assert len(json.loads(outcomes_path.read_text(encoding="utf-8"))) == 3
