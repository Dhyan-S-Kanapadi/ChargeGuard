from datetime import datetime, timedelta, timezone

from agents.acceptance import accept_and_log_agent
from agents.escalation import human_escalation_agent
from core.state import ChargebackState


def _state() -> ChargebackState:
    now = datetime(2026, 5, 12, tzinfo=timezone.utc)
    return {
        "chargeback_id": "cb_control_001",
        "order_id": "order_demo_001",
        "payment_id": "pay_demo_001",
        "tracking_id": "trk_demo_001",
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
        "win_probability": 0.18,
        "expected_value": 435.0,
        "decision": "ACCEPT",
        "decision_reasoning": "Weak evidence.",
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


def test_accept_and_log_agent_records_no_filing_loss() -> None:
    result = accept_and_log_agent(_state())

    assert result["decision"] == "ACCEPT"
    assert result["filing_confirmation"] == "accepted_no_filing"
    assert result["final_outcome"] == "LOSS"
    assert result["outcome_reason"] == "Chargeback accepted because representment was not economically justified."


def test_human_escalation_agent_records_pending_review() -> None:
    state = _state()
    state["quality_rejection_reason"] = "Missing required evidence: shipping."

    result = human_escalation_agent(state)

    assert result["filing_confirmation"] == "human_review_required"
    assert result["final_outcome"] == "PENDING"
    assert result["outcome_reason"] == (
        "Human review required after automated quality rejection: Missing required evidence: shipping."
    )
