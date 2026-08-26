from datetime import datetime, timezone

from ml.subscores import subscores_from_state
from ml.train import train_baseline_model


def _state() -> dict:
    return {
        "chargeback_id": "cb_subscore_001",
        "reason_code": "10.4",
        "card_network": "VISA",
        "dispute_amount": 2_500.0,
        "currency": "INR",
        "filing_deadline": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "merchant_profile": {},
        "transaction": {
            "otp_verified": True,
            "three_ds_authenticated": True,
            "order_history_count": 50,
            "previous_chargebacks": 0,
        },
        "shipping": {
            "status": "DELIVERED",
            "signature_obtained": True,
        },
        "device": {
            "fraud_score": 0.0,
            "vpn_detected": False,
        },
        "consortium": {
            "ethoca_match": False,
            "verifi_match": False,
            "cross_merchant_fraud_history": False,
        },
    }


def test_subscores_use_model_coefficient_directions(tmp_path, monkeypatch) -> None:
    model_path = tmp_path / "model.pkl"
    train_baseline_model(output_path=model_path, count=200, seed=42)
    monkeypatch.setenv("MODEL_PATH", str(model_path))

    strong_evidence_scores = subscores_from_state(_state())
    risk_state = _state()
    risk_state["transaction"] = {
        "otp_verified": False,
        "three_ds_authenticated": False,
        "order_history_count": 0,
        "previous_chargebacks": 0,
    }
    risk_state["shipping"] = {"status": "PENDING", "signature_obtained": False}
    risk_state["device"] = {"fraud_score": 100.0, "vpn_detected": True}
    risk_state["consortium"] = {
        "ethoca_match": True,
        "verifi_match": False,
        "cross_merchant_fraud_history": True,
    }
    risk_scores = subscores_from_state(risk_state)

    assert strong_evidence_scores["third_party_fraud_indicators"]["label"] == "low"
    assert strong_evidence_scores["identity_continuity"]["label"] == "high"
    assert risk_scores["third_party_fraud_indicators"]["label"] == "high"
    assert risk_scores["identity_continuity"]["label"] == "low"
    assert (
        strong_evidence_scores["third_party_fraud_indicators"]["score"]
        < risk_scores["third_party_fraud_indicators"]["score"]
    )
    assert (
        strong_evidence_scores["identity_continuity"]["score"]
        > risk_scores["identity_continuity"]["score"]
    )
