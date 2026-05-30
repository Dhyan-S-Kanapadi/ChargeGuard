from datetime import datetime, timedelta, timezone

from agents.evidence import shipping
from agents.evidence.shipping import _build_shipping_evidence, shipping_agent
from core.state import ChargebackState


def _state() -> ChargebackState:
    now = datetime(2026, 5, 12, tzinfo=timezone.utc)
    return {
        "chargeback_id": "cb_ship_001",
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


def test_shipping_agent_populates_only_shipping_evidence() -> None:
    state = _state()
    result = shipping_agent(state)

    assert result["shipping"] is not None
    assert result["shipping"]["tracking_id"] == "trk_demo_001"
    assert result["shipping"]["courier"] == "Shiprocket"
    assert result["shipping"]["status"] == "DELIVERED"
    assert result["shipping"]["delivery_latitude"] == 12.9716
    assert result["shipping"]["signature_obtained"] is True
    assert result["transaction"] is None
    assert result["device"] is None


def test_shipping_builder_accepts_provider_payload_variants() -> None:
    tracking = {
        "awb": "awb_variant_001",
        "carrier": "Delhivery",
        "current_status": "Delivered",
        "delivery_time": "2026-05-20T10:15:00Z",
        "gps": {
            "lat": 19.076,
            "lng": 72.8777,
        },
        "pod": {
            "signature": "signed",
            "image_url": "https://example.test/pod/awb_variant_001.jpg",
        },
    }

    evidence = _build_shipping_evidence(tracking)

    assert evidence["tracking_id"] == "awb_variant_001"
    assert evidence["courier"] == "Delhivery"
    assert evidence["status"] == "Delivered"
    assert evidence["delivered_at"] is not None
    assert evidence["delivered_at"].tzinfo is not None
    assert evidence["delivery_latitude"] == 19.076
    assert evidence["delivery_longitude"] == 72.8777
    assert evidence["signature_obtained"] is True
    assert evidence["delivery_photo_url"] == "https://example.test/pod/awb_variant_001.jpg"


def test_shipping_agent_records_empty_evidence_on_collection_failure(monkeypatch) -> None:
    def fail_tracking_collection(state: ChargebackState) -> dict:
        raise RuntimeError("carrier unavailable")

    monkeypatch.setattr(shipping, "_stub_tracking_response", fail_tracking_collection)

    state = _state()
    result = shipping_agent(state)

    assert result["shipping"] is not None
    assert result["shipping"]["status"] == "UNKNOWN"
    assert result["shipping"]["signature_obtained"] is False
    assert result["shipping"]["raw"]["source"] == "shipping_agent_empty"
    assert result["shipping"]["raw"]["error"] == "carrier unavailable"
    assert result["transaction"] is None
