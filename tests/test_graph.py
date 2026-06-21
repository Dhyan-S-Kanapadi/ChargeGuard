from datetime import datetime, timedelta, timezone

from core.graph import app
from core.state import ChargebackState
from ml.train import train_baseline_model


def test_chargeback_graph_runs_from_start_to_end(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REBUTTAL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "true")
    model_path = tmp_path / "win_probability_model.pkl"
    train_baseline_model(output_path=model_path, count=200, seed=42)
    monkeypatch.setenv("MODEL_PATH", str(model_path))

    now = datetime(2026, 5, 12, tzinfo=timezone.utc)
    state: ChargebackState = {
        "chargeback_id": "cb_test_001",
        "reason_code": "10.4",
        "card_network": "VISA",
        "dispute_amount": 2500.0,
        "currency": "INR",
        "filing_deadline": now + timedelta(days=30),
        "merchant_profile": {
            "merchant_id": "merchant_001",
            "name": "Demo Merchant",
            "vertical": "ecommerce",
            "razorpay_key": "rzp_test_demo",
            "shiprocket_key": "shiprocket_demo",
            "freshdesk_domain": "demo.freshdesk.com",
            "average_order_value": 1800.0,
            "chargeback_history_count": 4,
        },
        "investigation_plan": {},
        "requires_food_agents": False,
        "transaction": None,
        "shipping": None,
        "comms": None,
        "device": None,
        "consortium": None,
        "delivery_photo": None,
        "order_timeline": None,
        "win_probability": None,
        "expected_value": None,
        "decision": "ACCEPT",
        "decision_reasoning": None,
        "rebuttal_document_path": None,
        "quality_approved": False,
        "quality_rejection_reason": None,
        "quality_loop_count": 0,
        "filing_confirmation": None,
        "filed_at": None,
        "final_outcome": None,
        "outcome_reason": None,
        "outcome_recorded_at": None,
    }

    result = app.invoke(state)

    assert result["chargeback_id"] == "cb_test_001"
    assert result["decision"] == "FIGHT"
    assert result["transaction"] is not None
    assert result["shipping"] is not None
    assert result["device"] is not None
    assert result["comms"] is not None
    assert result["consortium"] is not None
    assert result["quality_approved"] is True
    assert result["rebuttal_document_path"] is not None
    assert result["filing_confirmation"] is not None
    assert result["filing_confirmation"].startswith("filed_visa_cb_test_001_")
