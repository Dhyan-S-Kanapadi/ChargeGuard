from datetime import datetime, timedelta, timezone

from api.store import InMemoryStore
from core.state import ChargebackState, MerchantProfile


def _merchant() -> MerchantProfile:
    return {
        "merchant_id": "merchant_store_001",
        "name": "Store Merchant",
        "vertical": "quick_commerce",
        "freshdesk_domain": "store.freshdesk.com",
        "average_order_value": 900.0,
        "chargeback_history_count": 2,
    }


def _state() -> ChargebackState:
    now = datetime(2026, 7, 6, 9, 30, tzinfo=timezone.utc)
    return {
        "chargeback_id": "cb_store_001",
        "order_id": "order_store_001",
        "payment_id": "pay_store_001",
        "tracking_id": "trk_store_001",
        "chargeback_received_at": now,
        "reason_code": "13.1",
        "card_network": "VISA",
        "dispute_amount": 1200.0,
        "currency": "INR",
        "filing_deadline": now + timedelta(days=30),
        "merchant_profile": _merchant(),
        "investigation_plan": {},
        "requires_food_agents": True,
        "transaction": None,
        "shipping": {
            "tracking_id": "trk_store_001",
            "courier": "Shiprocket",
            "status": "DELIVERED",
            "delivered_at": now + timedelta(hours=1),
            "delivery_latitude": 12.9716,
            "delivery_longitude": 77.5946,
            "signature_obtained": True,
            "delivery_photo_url": "https://example.test/pod.jpg",
            "raw": {},
        },
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


def test_store_persists_merchants_and_disputes(tmp_path) -> None:
    store_path = tmp_path / "chargeguard_store.json"
    store = InMemoryStore(path=store_path)

    assert store.create_merchant(_merchant()) is True
    assert store.create_dispute(_state()) is True
    store.update_dispute("cb_store_001", status="completed", state=_state())

    reloaded = InMemoryStore(path=store_path)
    merchant = reloaded.get_merchant("merchant_store_001")
    dispute = reloaded.get_dispute("cb_store_001")

    assert merchant is not None
    assert merchant["vertical"] == "quick_commerce"
    assert dispute is not None
    assert dispute["status"] == "completed"
    assert dispute["created_at"].tzinfo is not None
    assert dispute["state"]["filing_deadline"].tzinfo is not None
    assert dispute["state"]["shipping"]["delivered_at"].tzinfo is not None


def test_store_clear_persists_empty_state(tmp_path) -> None:
    store_path = tmp_path / "chargeguard_store.json"
    store = InMemoryStore(path=store_path)
    assert store.create_merchant(_merchant()) is True

    store.clear()

    reloaded = InMemoryStore(path=store_path)
    assert reloaded.get_merchant("merchant_store_001") is None
    assert reloaded.list_disputes() == []
