from datetime import datetime, timedelta, timezone

from agents.evidence import comms
from agents.evidence.comms import _build_comms_evidence, comms_agent
from core.state import ChargebackState


def _state() -> ChargebackState:
    now = datetime(2026, 5, 12, tzinfo=timezone.utc)
    return {
        "chargeback_id": "cb_comms_001",
        "order_id": "order_demo_001",
        "payment_id": "pay_demo_001",
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
        "transaction": {
            "order_id": "order_demo_001",
            "payment_id": "pay_demo_001",
            "amount": 2500.0,
            "currency": "INR",
            "otp_verified": True,
            "three_ds_authenticated": True,
            "device_id": "device_demo_123",
            "ip_address": "49.36.18.22",
            "customer_email": "buyer@example.com",
            "order_history_count": 8,
            "previous_chargebacks": 0,
            "raw": {},
        },
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


def test_comms_agent_populates_only_comms_evidence() -> None:
    state = _state()
    result = comms_agent(state)

    assert result["comms"] is not None
    assert len(result["comms"]["emails"]) == 2
    assert result["comms"]["emails"][0]["to"] == "buyer@example.com"
    assert result["comms"]["post_delivery_interaction"] is True
    assert result["comms"]["complaint_raised_before_chargeback"] is False
    assert result["shipping"] is None
    assert result["device"] is None


def test_comms_builder_detects_pre_chargeback_complaints() -> None:
    state = _state()
    emails = [
        {
            "from": "buyer@example.com",
            "direction": "inbound",
            "category": "post_delivery_interaction",
        }
    ]
    support_tickets = [
        {
            "id": "ticket_001",
            "type": "pre_chargeback_complaint",
            "raised_before_chargeback": True,
        }
    ]

    evidence = _build_comms_evidence(state, emails, support_tickets)

    assert evidence["post_delivery_interaction"] is True
    assert evidence["complaint_raised_before_chargeback"] is True
    assert evidence["support_tickets"] == support_tickets


def test_comms_agent_records_empty_evidence_on_collection_failure(monkeypatch) -> None:
    def fail_email_collection(state: ChargebackState) -> list[dict]:
        raise RuntimeError("gmail unavailable")

    monkeypatch.setattr(comms, "_stub_email_response", fail_email_collection)

    state = _state()
    result = comms_agent(state)

    assert result["comms"] is not None
    assert result["comms"]["emails"] == []
    assert result["comms"]["post_delivery_interaction"] is False
    assert result["comms"]["raw"]["source"] == "comms_agent_empty"
    assert result["comms"]["raw"]["error"] == "gmail unavailable"
