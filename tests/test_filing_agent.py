from datetime import datetime, timedelta, timezone

from agents.filing import filing_agent
from core.state import ChargebackState


def _state() -> ChargebackState:
    now = datetime(2026, 5, 12, tzinfo=timezone.utc)
    return {
        "chargeback_id": "cb_filing_001",
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
        "quality_approved": False,
        "quality_rejection_reason": None,
        "quality_loop_count": 0,
        "filing_confirmation": None,
        "filed_at": None,
        "final_outcome": None,
        "outcome_reason": None,
        "outcome_recorded_at": None,
    }


def test_filing_agent_records_confirmation_for_approved_packet(tmp_path) -> None:
    path = tmp_path / "rebuttal.json"
    path.write_text("{}", encoding="utf-8")
    state = _state()
    state["quality_approved"] = True
    state["rebuttal_document_path"] = str(path)

    result = filing_agent(state)

    assert result["filed_at"] is not None
    assert result["filing_confirmation"] is not None
    assert result["filing_confirmation"].startswith("filed_visa_cb_filing_001_")


def test_filing_agent_blocks_unapproved_packets(tmp_path) -> None:
    path = tmp_path / "rebuttal.json"
    path.write_text("{}", encoding="utf-8")
    state = _state()
    state["quality_approved"] = False
    state["rebuttal_document_path"] = str(path)

    result = filing_agent(state)

    assert result["filed_at"] is None
    assert result["filing_confirmation"] == "filing_blocked_quality_not_approved"


def test_filing_agent_blocks_missing_rebuttal_document() -> None:
    state = _state()
    state["quality_approved"] = True
    state["rebuttal_document_path"] = "missing.json"

    result = filing_agent(state)

    assert result["filed_at"] is None
    assert result["filing_confirmation"] == "filing_blocked_missing_rebuttal"
