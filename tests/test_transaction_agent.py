from datetime import datetime, timedelta, timezone

from agents.evidence import transaction
from agents.evidence.transaction import _build_transaction_evidence, transaction_agent
from core.state import ChargebackState


def _state() -> ChargebackState:
    now = datetime(2026, 5, 12, tzinfo=timezone.utc)
    return {
        "chargeback_id": "cb_txn_001",
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


def test_transaction_agent_populates_only_transaction_evidence() -> None:
    state = _state()
    result = transaction_agent(state)

    assert result["transaction"] is not None
    assert result["transaction"]["order_id"] == "order_demo_001"
    assert result["transaction"]["payment_id"] == "pay_demo_001"
    assert result["transaction"]["amount"] == 2500.0
    assert result["transaction"]["otp_verified"] is True
    assert result["transaction"]["three_ds_authenticated"] is True
    assert result["shipping"] is None
    assert result["device"] is None


def test_transaction_builder_accepts_provider_payload_variants() -> None:
    state = _state()
    payment = {
        "id": "pay_variant_001",
        "order_id": "order_variant_001",
        "amount": "349900",
        "currency": "INR",
        "customer_email": "buyer@example.com",
        "metadata": {
            "device_id": "device_variant_123",
            "ip_address": "103.21.244.7",
        },
        "authentication": {
            "otp_verified": "verified",
            "three_ds": {
                "authenticated": "true",
            },
        },
    }
    order = {
        "id": "order_variant_001",
        "order_history_count": 5,
        "previous_chargebacks": 1,
    }

    evidence = _build_transaction_evidence(state, payment, order)

    assert evidence["amount"] == 3499.0
    assert evidence["customer_email"] == "buyer@example.com"
    assert evidence["device_id"] == "device_variant_123"
    assert evidence["ip_address"] == "103.21.244.7"
    assert evidence["otp_verified"] is True
    assert evidence["three_ds_authenticated"] is True
    assert evidence["order_history_count"] == 5
    assert evidence["previous_chargebacks"] == 1


def test_transaction_agent_records_empty_evidence_on_collection_failure(monkeypatch) -> None:
    def fail_payment_collection(state: ChargebackState) -> dict:
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(transaction, "_stub_payment_response", fail_payment_collection)

    state = _state()
    result = transaction_agent(state)

    assert result["transaction"] is not None
    assert result["transaction"]["amount"] == 0.0
    assert result["transaction"]["otp_verified"] is False
    assert result["transaction"]["raw"]["source"] == "transaction_agent_empty"
    assert result["transaction"]["raw"]["error"] == "provider unavailable"
    assert result["shipping"] is None
