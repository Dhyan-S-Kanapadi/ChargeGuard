from datetime import datetime, timedelta, timezone

from agents.quality_check import quality_check_agent
from agents.rebuttal_builder import rebuttal_builder_agent
from core.graph import route_quality
from core.state import ChargebackState


def _amex_state() -> ChargebackState:
    now = datetime(2026, 5, 12, tzinfo=timezone.utc)
    return {
        "chargeback_id": "cb_amex_001",
        "reason_code": "13.1",
        "card_network": "AMEX",
        "dispute_amount": 2500.0,
        "currency": "INR",
        "filing_deadline": now + timedelta(days=30),
        "merchant_profile": {
            "merchant_id": "merchant_001",
            "name": "Demo Merchant",
            "vertical": "ecommerce",
            "freshdesk_domain": "",
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


def test_unsupported_card_network_does_not_crash(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REBUTTAL_OUTPUT_DIR", str(tmp_path))

    result = rebuttal_builder_agent(_amex_state())

    assert result["rebuttal_document_path"] is None
    assert result["rebuttal_build_error"] == "unsupported_card_network"


def test_unsupported_card_network_escalates_without_wasting_retries(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REBUTTAL_OUTPUT_DIR", str(tmp_path))
    state = rebuttal_builder_agent(_amex_state())

    result = quality_check_agent(state)

    assert result["quality_approved"] is False
    assert result["quality_rejection_reason"] == "unsupported_card_network"
    assert result["quality_auto_fixable"] is False
    assert route_quality(result) == "escalate"
    assert result.get("quality_loop_count", 0) == 0
