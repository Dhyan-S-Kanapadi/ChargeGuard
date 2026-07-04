from datetime import datetime, timedelta, timezone

from agents.evidence import delivery_photo
from agents.evidence.delivery_photo import _build_delivery_photo_evidence, delivery_photo_agent
from core.state import ChargebackState


def _state() -> ChargebackState:
    now = datetime(2026, 5, 12, tzinfo=timezone.utc)
    delivered_at = now + timedelta(days=2)
    return {
        "chargeback_id": "cb_photo_001",
        "order_id": "order_demo_001",
        "payment_id": "pay_demo_001",
        "reason_code": "13.1",
        "card_network": "VISA",
        "dispute_amount": 2500.0,
        "currency": "INR",
        "filing_deadline": now + timedelta(days=30),
        "merchant_profile": {
            "merchant_id": "merchant_001",
            "name": "Demo Merchant",
            "vertical": "food_delivery",
            "razorpay_key": "rzp_test_demo",
            "shiprocket_key": "shiprocket_demo",
            "freshdesk_domain": "demo.freshdesk.com",
            "average_order_value": 1800.0,
            "chargeback_history_count": 4,
        },
        "investigation_plan": {},
        "requires_food_agents": True,
        "transaction": None,
        "shipping": {
            "tracking_id": "trk_demo_001",
            "courier": "Shiprocket",
            "status": "DELIVERED",
            "delivered_at": delivered_at,
            "delivery_latitude": 12.9716,
            "delivery_longitude": 77.5946,
            "signature_obtained": True,
            "delivery_photo_url": "https://example.test/pod/trk_demo_001.jpg",
            "raw": {},
        },
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


def test_delivery_photo_agent_populates_only_delivery_photo_evidence(monkeypatch) -> None:
    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "true")

    state = _state()
    result = delivery_photo_agent(state)

    assert result["delivery_photo"] is not None
    assert result["delivery_photo"]["photo_url"] == "https://example.test/pod/trk_demo_001.jpg"
    assert result["delivery_photo"]["ai_verified"] is True
    assert result["delivery_photo"]["address_visible"] is True
    assert result["delivery_photo"]["timestamp_on_photo"] == state["shipping"]["delivered_at"]
    assert result["order_timeline"] is None
    assert result["device"] is None


def test_delivery_photo_builder_accepts_vision_payload_variants() -> None:
    captured_at = datetime(2026, 5, 14, 10, 15, tzinfo=timezone.utc)
    response = {
        "image_url": "https://example.test/pod/image.jpg",
        "delivery_match": True,
        "doorstep_visible": True,
        "captured_at": captured_at,
    }

    evidence = _build_delivery_photo_evidence(response)

    assert evidence["photo_url"] == "https://example.test/pod/image.jpg"
    assert evidence["ai_verified"] is True
    assert evidence["address_visible"] is True
    assert evidence["timestamp_on_photo"] == captured_at


def test_delivery_photo_agent_collects_platform_photo_and_claude_vision(monkeypatch) -> None:
    monkeypatch.delenv("CHARGEGUARD_USE_STUBS", raising=False)

    class FakePlatformClient:
        @classmethod
        def from_env(cls):
            return cls()

        def get_delivery_photo(self, order_id: str) -> dict:
            assert order_id == "order_demo_001"
            return {
                "photo_url": "https://example.test/platform/pod.jpg",
                "captured_at": "2026-05-14T10:15:00Z",
            }

    class FakeVisionClient:
        @classmethod
        def from_env(cls):
            return cls()

        def verify_delivery_photo(self, photo_url: str) -> dict:
            assert photo_url == "https://example.test/platform/pod.jpg"
            return {
                "delivered": True,
                "address_visible": True,
                "confidence": 0.93,
            }

    state = _state()
    state["shipping"]["delivery_photo_url"] = None
    monkeypatch.setattr(delivery_photo, "FoodPlatformClient", FakePlatformClient)
    monkeypatch.setattr(delivery_photo, "ClaudeVisionClient", FakeVisionClient)

    result = delivery_photo_agent(state)

    assert result["delivery_photo"] is not None
    assert result["delivery_photo"]["photo_url"] == "https://example.test/platform/pod.jpg"
    assert result["delivery_photo"]["ai_verified"] is True
    assert result["delivery_photo"]["address_visible"] is True
    assert result["delivery_photo"]["timestamp_on_photo"].isoformat() == "2026-05-14T10:15:00+00:00"
    assert result["delivery_photo"]["raw"]["source"] == "claude_vision"


def test_delivery_photo_agent_records_empty_evidence_on_collection_failure(monkeypatch) -> None:
    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "true")

    def fail_photo_verification(state: ChargebackState) -> dict:
        raise RuntimeError("vision unavailable")

    monkeypatch.setattr(delivery_photo, "_stub_photo_verification_response", fail_photo_verification)

    state = _state()
    result = delivery_photo_agent(state)

    assert result["delivery_photo"] is not None
    assert result["delivery_photo"]["photo_url"] == "https://example.test/pod/trk_demo_001.jpg"
    assert result["delivery_photo"]["ai_verified"] is False
    assert result["delivery_photo"]["address_visible"] is False
    assert result["delivery_photo"]["raw"]["source"] == "delivery_photo_agent_empty"
    assert result["delivery_photo"]["raw"]["error"] == "vision unavailable"
