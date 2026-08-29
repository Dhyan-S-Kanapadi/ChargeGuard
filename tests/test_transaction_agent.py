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


def test_transaction_agent_populates_only_transaction_evidence(monkeypatch) -> None:
    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "true")
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


def test_transaction_provider_override_keeps_razorpay_stubbed(monkeypatch) -> None:
    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "false")
    monkeypatch.setenv("RAZORPAY_USE_STUBS", "true")

    result = transaction_agent(_state())

    assert result["transaction"] is not None
    assert result["transaction"]["raw"]["source"] == "transaction_agent_stub"


def test_transaction_live_override_marks_missing_credentials_degraded(monkeypatch) -> None:
    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "true")
    monkeypatch.setenv("RAZORPAY_USE_STUBS", "false")
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)

    result = transaction_agent(_state())

    assert result["transaction"] is not None
    assert result["transaction"]["raw"]["source"] == "transaction_agent_empty"
    assert result["evidence_collection_degraded"] is True
    assert "razorpay_credentials_missing" in result["degraded_reasons"]


def test_transaction_uses_global_stub_fallback_when_override_is_unset(monkeypatch) -> None:
    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "true")
    monkeypatch.delenv("RAZORPAY_USE_STUBS", raising=False)

    result = transaction_agent(_state())

    assert result["transaction"] is not None
    assert result["transaction"]["raw"]["source"] == "transaction_agent_stub"


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


def test_transaction_agent_collects_razorpay_evidence(monkeypatch) -> None:
    class FakeRazorpayClient:
        def get_payment(self, payment_id: str) -> dict:
            return {
                "id": payment_id,
                "order_id": "order_demo_001",
                "amount": 250000,
                "currency": "INR",
                "email": "real@example.com",
                "three_ds_authenticated": True,
                "otp_verified": True,
            }

        def get_order(self, order_id: str) -> dict:
            return {"id": order_id, "customer": {"email": "real@example.com"}}

    monkeypatch.delenv("CHARGEGUARD_USE_STUBS", raising=False)
    monkeypatch.setattr(
        transaction.RazorpayClient,
        "from_env",
        classmethod(lambda cls: FakeRazorpayClient()),
    )

    result = transaction_agent(_state())

    assert result["transaction"] is not None
    assert result["transaction"]["customer_email"] == "real@example.com"
    assert result["transaction"]["raw"]["source"] == "razorpay"


def test_transaction_agent_normalizes_stripe_evidence(monkeypatch) -> None:
    class FakeStripeClient:
        def get_payment_intent(self, payment_id: str) -> dict:
            return {
                "id": payment_id,
                "amount": 4200,
                "currency": "usd",
                "receipt_email": "stripe@example.com",
                "latest_charge": "ch_demo_001",
                "status": "succeeded",
                "metadata": {
                    "order_id": "order_stripe_001",
                    "device_id": "stripe_device_001",
                    "order_history_count": "3",
                },
            }

        def get_charge(self, charge_id: str) -> dict:
            return {
                "id": charge_id,
                "payment_method_details": {
                    "card": {
                        "three_d_secure": {
                            "result": "authenticated",
                        }
                    }
                },
            }

    state = _state()
    state["currency"] = "USD"
    state["merchant_profile"]["payment_provider"] = "stripe"
    monkeypatch.delenv("CHARGEGUARD_USE_STUBS", raising=False)
    monkeypatch.setattr(
        transaction.StripeClient,
        "from_env",
        classmethod(lambda cls: FakeStripeClient()),
    )

    result = transaction_agent(state)

    assert result["transaction"] is not None
    assert result["transaction"]["amount"] == 42.0
    assert result["transaction"]["currency"] == "USD"
    assert result["transaction"]["three_ds_authenticated"] is True
    assert result["transaction"]["order_history_count"] == 3
    assert result["transaction"]["raw"]["source"] == "stripe"


def test_transaction_agent_records_empty_evidence_on_collection_failure(monkeypatch) -> None:
    def fail_collection(state: ChargebackState) -> tuple[dict, dict, str]:
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(transaction, "_collect_transaction_data", fail_collection)

    state = _state()
    result = transaction_agent(state)

    assert result["transaction"] is not None
    assert result["transaction"]["amount"] == 0.0
    assert result["transaction"]["otp_verified"] is False
    assert result["transaction"]["raw"]["source"] == "transaction_agent_empty"
    assert result["transaction"]["raw"]["error"] == "provider unavailable"
    assert result["shipping"] is None
