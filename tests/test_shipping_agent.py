from datetime import datetime, timedelta, timezone

from agents.evidence.shipping import shipping_agent
from core.state import ChargebackState


def _state() -> ChargebackState:
    now = datetime(2026, 5, 12, tzinfo=timezone.utc)
    return {
        "chargeback_id": "cb_ship_001",
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


def test_shipping_agent_populates_only_shipping_evidence() -> None:
    state = _state()
    result = shipping_agent(state)

    assert result["shipping"] is not None
    assert result["shipping"]["tracking_id"] == "trk_demo_001"
    assert result["shipping"]["courier"] == "Shiprocket"
    assert result["shipping"]["status"] == "DELIVERED"
    assert result["shipping"]["delivery_latitude"] == 12.9716
    assert result["shipping"]["signature_obtained"] is True
    assert result["transaction"] is None
    assert result["device"] is None
