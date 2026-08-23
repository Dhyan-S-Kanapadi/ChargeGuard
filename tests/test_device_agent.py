from datetime import datetime, timedelta, timezone

from agents.evidence import device
from agents.evidence.device import _build_device_evidence, device_agent
from core.state import ChargebackState


def _state() -> ChargebackState:
    now = datetime(2026, 5, 12, tzinfo=timezone.utc)
    return {
        "chargeback_id": "cb_device_001",
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


def test_device_agent_populates_only_device_evidence(monkeypatch) -> None:
    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "true")
    state = _state()
    result = device_agent(state)

    assert result["device"] is not None
    assert result["device"]["fraud_score"] == 18.0
    assert result["device"]["device_fingerprint"] == "device_demo_123"
    assert result["device"]["geolocation_match"] is True
    assert result["device"]["login_pattern_normal"] is True
    assert result["device"]["vpn_detected"] is False
    assert result["shipping"] is None
    assert result["comms"] is None


def test_device_builder_accepts_provider_payload_variants() -> None:
    risk = {
        "score": 72,
        "fingerprint": "fingerprint_variant_001",
        "geolocation_match": True,
        "login_pattern_normal": False,
        "vpn_detected": True,
    }

    evidence = _build_device_evidence(risk)

    assert evidence["fraud_score"] == 72.0
    assert evidence["device_fingerprint"] == "fingerprint_variant_001"
    assert evidence["geolocation_match"] is True
    assert evidence["login_pattern_normal"] is False
    assert evidence["vpn_detected"] is True


def test_device_agent_marks_collection_failure_as_degraded(monkeypatch) -> None:
    def fail_device_collection(state: ChargebackState) -> tuple[dict, str]:
        raise RuntimeError("seon unavailable")

    monkeypatch.setattr(device, "_collect_device_data", fail_device_collection)

    state = _state()
    result = device_agent(state)

    assert result["device"] is None
    assert result["evidence_collection_degraded"] is True
    assert result["degraded_reasons"] == ["device"]


def test_device_agent_collects_and_normalizes_seon(monkeypatch) -> None:
    class FakeSeonClient:
        def fraud_check(self, payload: dict) -> dict:
            assert payload["ip"] == "49.36.18.22"
            assert payload["device_id"] == "device_demo_123"
            assert payload["email"] == "buyer@example.com"
            return {
                "success": True,
                "data": {
                    "fraud_score": 24,
                    "device_details": {"device_hash": "seon_hash_123"},
                    "ip_details": {
                        "is_vpn": False,
                        "latitude": 12.9717,
                        "longitude": 77.5947,
                    },
                    "behavior": {"is_normal": True},
                },
            }

    state = _state()
    state["shipping"] = {
        "tracking_id": "trk_demo_001",
        "courier": "Shiprocket",
        "status": "DELIVERED",
        "delivered_at": None,
        "delivery_latitude": 12.9716,
        "delivery_longitude": 77.5946,
        "signature_obtained": True,
        "delivery_photo_url": None,
        "raw": {},
    }
    monkeypatch.delenv("CHARGEGUARD_USE_STUBS", raising=False)
    monkeypatch.setattr(
        device.SeonClient,
        "from_env",
        classmethod(lambda cls: FakeSeonClient()),
    )

    result = device_agent(state)

    assert result["device"] is not None
    assert result["device"]["fraud_score"] == 24.0
    assert result["device"]["device_fingerprint"] == "seon_hash_123"
    assert result["device"]["geolocation_match"] is True
    assert result["device"]["login_pattern_normal"] is True
    assert result["device"]["vpn_detected"] is False
    assert result["device"]["raw"]["source"] == "seon"
