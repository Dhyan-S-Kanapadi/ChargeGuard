from datetime import datetime, timedelta, timezone

from agents.scoring import scoring_agent
from core.state import ChargebackState


def _state() -> ChargebackState:
    now = datetime(2026, 5, 12, tzinfo=timezone.utc)
    return {
        "chargeback_id": "cb_score_001",
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


def _add_strong_evidence(state: ChargebackState) -> ChargebackState:
    state["transaction"] = {
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
    }
    state["shipping"] = {
        "tracking_id": "trk_demo_001",
        "courier": "Shiprocket",
        "status": "DELIVERED",
        "delivered_at": state["filing_deadline"] - timedelta(days=12),
        "delivery_latitude": 12.9716,
        "delivery_longitude": 77.5946,
        "signature_obtained": True,
        "delivery_photo_url": "https://example.test/pod/trk_demo_001.jpg",
        "raw": {},
    }
    state["device"] = {
        "fraud_score": 18.0,
        "device_fingerprint": "device_demo_123",
        "geolocation_match": True,
        "login_pattern_normal": True,
        "vpn_detected": False,
        "raw": {},
    }
    state["comms"] = {
        "emails": [],
        "support_tickets": [],
        "post_delivery_interaction": True,
        "complaint_raised_before_chargeback": False,
        "raw": {},
    }
    state["consortium"] = {
        "ethoca_match": False,
        "verifi_match": False,
        "cross_merchant_fraud_history": False,
        "dispute_count_across_merchants": 0,
        "raw": {},
    }
    return state


def test_scoring_agent_fights_when_evidence_is_strong(monkeypatch) -> None:
    monkeypatch.delenv("RESPONSE_COST_USD", raising=False)
    monkeypatch.delenv("FIGHT_EV_THRESHOLD", raising=False)

    result = scoring_agent(_add_strong_evidence(_state()))

    assert result["decision"] == "FIGHT"
    assert result["win_probability"] is not None
    assert result["win_probability"] >= 0.5
    assert result["expected_value"] is not None
    assert result["expected_value"] > 0
    assert "otp_verified" in result["decision_reasoning"]


def test_scoring_agent_accepts_when_evidence_is_weak(monkeypatch) -> None:
    monkeypatch.delenv("RESPONSE_COST_USD", raising=False)
    monkeypatch.delenv("FIGHT_EV_THRESHOLD", raising=False)

    result = scoring_agent(_state())

    assert result["decision"] == "ACCEPT"
    assert result["win_probability"] == 0.18
    assert result["expected_value"] == 435.0
    assert result["decision_reasoning"] == "Win probability 18%; expected value 435.00 INR; matched signals: none."


def test_scoring_agent_respects_expected_value_threshold(monkeypatch) -> None:
    monkeypatch.setenv("FIGHT_EV_THRESHOLD", "10000")
    monkeypatch.setenv("RESPONSE_COST_USD", "15")

    result = scoring_agent(_add_strong_evidence(_state()))

    assert result["decision"] == "ACCEPT"
    assert result["expected_value"] is not None
    assert result["expected_value"] < 10000
