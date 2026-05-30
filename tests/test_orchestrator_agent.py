from datetime import datetime, timedelta, timezone

from agents.orchestrator import _build_investigation_plan, orchestrator_agent
from core.state import ChargebackState


def _state() -> ChargebackState:
    now = datetime(2026, 5, 12, tzinfo=timezone.utc)
    return {
        "chargeback_id": "cb_orch_001",
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
        "win_probability": None,
        "expected_value": None,
        "decision": None,
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


def test_orchestrator_builds_standard_ecommerce_plan() -> None:
    state = _state()
    result = orchestrator_agent(state)

    assert result["requires_food_agents"] is False
    assert result["investigation_plan"]["evidence_tasks"] == [
        "transaction",
        "shipping",
        "device",
        "comms",
        "consortium",
    ]
    assert result["investigation_plan"]["routing"]["food_evidence"] is False


def test_orchestrator_routes_food_reason_codes_to_food_evidence() -> None:
    state = _state()
    state["reason_code"] = "13.1"

    result = orchestrator_agent(state)

    assert result["requires_food_agents"] is True
    assert "delivery_photo" in result["investigation_plan"]["evidence_tasks"]
    assert "order_timeline" in result["investigation_plan"]["evidence_tasks"]
    assert result["investigation_plan"]["routing"]["food_evidence"] is True


def test_investigation_plan_assigns_deadline_priority() -> None:
    state = _state()
    now = datetime(2026, 5, 12, tzinfo=timezone.utc)
    state["filing_deadline"] = now + timedelta(days=2)

    plan = _build_investigation_plan(state, now=now)

    assert plan["days_until_deadline"] == 2
    assert plan["priority"] == "urgent"
