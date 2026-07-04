from datetime import datetime, timedelta, timezone

from agents.evidence import order_timeline
from agents.evidence.order_timeline import _build_order_timeline_evidence, order_timeline_agent
from core.state import ChargebackState


def _state() -> ChargebackState:
    now = datetime(2026, 5, 12, tzinfo=timezone.utc)
    delivered_at = now + timedelta(days=2)
    return {
        "chargeback_id": "cb_timeline_001",
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


def test_order_timeline_agent_populates_only_order_timeline_evidence(monkeypatch) -> None:
    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "true")

    state = _state()
    result = order_timeline_agent(state)

    assert result["order_timeline"] is not None
    assert result["order_timeline"]["accepted_at"] is not None
    assert result["order_timeline"]["picked_at"] is not None
    assert result["order_timeline"]["delivered_at"] == state["shipping"]["delivered_at"]
    assert result["order_timeline"]["post_delivery_rating"] == 4.5
    assert result["delivery_photo"] is None
    assert result["device"] is None


def test_order_timeline_builder_accepts_string_timestamps() -> None:
    response = {
        "placed_at": "2026-05-14T08:00:00Z",
        "accepted_at": "2026-05-14T08:03:00Z",
        "picked_up_at": "2026-05-14T08:25:00Z",
        "delivered_at": "2026-05-14T09:10:00Z",
        "rating": 4.0,
    }

    evidence = _build_order_timeline_evidence(response)

    assert evidence["placed_at"].tzinfo is not None
    assert evidence["accepted_at"] is not None
    assert evidence["picked_at"] is not None
    assert evidence["delivered_at"] is not None
    assert evidence["post_delivery_rating"] == 4.0


def test_order_timeline_agent_collects_platform_timeline(monkeypatch) -> None:
    monkeypatch.delenv("CHARGEGUARD_USE_STUBS", raising=False)

    class FakePlatformClient:
        @classmethod
        def from_env(cls):
            return cls()

        def get_order_timeline(self, order_id: str) -> dict:
            assert order_id == "order_demo_001"
            return {
                "placed_at": "2026-05-14T08:00:00Z",
                "accepted_at": "2026-05-14T08:03:00Z",
                "picked_at": "2026-05-14T08:25:00Z",
                "delivered_at": "2026-05-14T09:10:00Z",
                "post_delivery_rating": 4.8,
            }

    monkeypatch.setattr(order_timeline, "FoodPlatformClient", FakePlatformClient)

    result = order_timeline_agent(_state())

    assert result["order_timeline"] is not None
    assert result["order_timeline"]["placed_at"].isoformat() == "2026-05-14T08:00:00+00:00"
    assert result["order_timeline"]["delivered_at"].isoformat() == "2026-05-14T09:10:00+00:00"
    assert result["order_timeline"]["post_delivery_rating"] == 4.8
    assert result["order_timeline"]["raw"]["source"] == "food_platform"


def test_order_timeline_agent_records_empty_evidence_on_collection_failure(monkeypatch) -> None:
    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "true")

    def fail_timeline_collection(state: ChargebackState) -> dict:
        raise RuntimeError("order system unavailable")

    monkeypatch.setattr(order_timeline, "_stub_order_timeline_response", fail_timeline_collection)

    state = _state()
    result = order_timeline_agent(state)

    assert result["order_timeline"] is not None
    assert result["order_timeline"]["accepted_at"] is None
    assert result["order_timeline"]["picked_at"] is None
    assert result["order_timeline"]["delivered_at"] is None
    assert result["order_timeline"]["raw"]["source"] == "order_timeline_agent_empty"
    assert result["order_timeline"]["raw"]["error"] == "order system unavailable"
