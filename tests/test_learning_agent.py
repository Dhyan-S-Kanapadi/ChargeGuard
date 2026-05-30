from datetime import datetime, timedelta, timezone

from agents.learning import learning_agent
from core.state import ChargebackState


def _state() -> ChargebackState:
    now = datetime(2026, 5, 12, tzinfo=timezone.utc)
    return {
        "chargeback_id": "cb_learning_001",
        "order_id": "order_demo_001",
        "payment_id": "pay_demo_001",
        "tracking_id": "trk_demo_001",
        "reason_code": "13.1",
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
        "win_probability": 0.82,
        "expected_value": 2035.0,
        "decision": "FIGHT",
        "decision_reasoning": "Strong evidence supports representment.",
        "rebuttal_document_path": None,
        "quality_approved": True,
        "quality_rejection_reason": None,
        "quality_loop_count": 1,
        "filing_confirmation": "filed_visa_cb_learning_001_20260512120000",
        "filed_at": now,
        "final_outcome": None,
        "outcome_reason": None,
        "outcome_recorded_at": None,
    }


def test_learning_agent_marks_unknown_outcome_pending() -> None:
    result = learning_agent(_state())

    assert result["final_outcome"] == "PENDING"
    assert result["outcome_reason"] == "Awaiting network decision."
    assert result["outcome_recorded_at"] is not None


def test_learning_agent_adds_win_reason_without_overwriting_outcome() -> None:
    state = _state()
    state["final_outcome"] = "WIN"

    result = learning_agent(state)

    assert result["final_outcome"] == "WIN"
    assert result["outcome_reason"] == "Representment won; evidence and scoring signals should be reinforced."
    assert result["outcome_recorded_at"] is not None


def test_learning_agent_preserves_existing_reason() -> None:
    state = _state()
    state["final_outcome"] = "LOSS"
    state["outcome_reason"] = "Issuer rejected delivery proof."

    result = learning_agent(state)

    assert result["final_outcome"] == "LOSS"
    assert result["outcome_reason"] == "Issuer rejected delivery proof."
    assert result["outcome_recorded_at"] is not None
