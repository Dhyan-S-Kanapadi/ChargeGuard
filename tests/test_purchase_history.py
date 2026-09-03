from datetime import datetime, timedelta, timezone
from typing import cast

import pytest

from agents.evidence.purchase_history import check_ce3_qualification
from api.store import store
from core.state import ChargebackState, MerchantProfile, OrderRecord


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _merchant() -> MerchantProfile:
    return {"merchant_id": "merchant_ce3", "name": "CE3 Merchant", "vertical": "ecommerce", "freshdesk_domain": "", "average_order_value": 0, "chargeback_history_count": 0}


def _order(order_id: str, age_days: int, **updates) -> OrderRecord:
    order: OrderRecord = {
        "order_id": order_id, "merchant_id": "merchant_ce3", "customer_email": "buyer@example.com",
        "customer_ip": "203.0.113.10", "user_agent": "Browser/1.0",
        "shipping_address": {"address1": "1 Main Street"}, "order_date": NOW - timedelta(days=age_days),
        "is_disputed": False, "is_fraud_flagged": False,
    }
    order.update(updates)
    return order


def _state() -> ChargebackState:
    return cast(ChargebackState, {"chargeback_id": "cb_ce3", "order_id": "current", "reason_code": "10.4", "card_network": "VISA", "merchant_profile": _merchant()})


@pytest.fixture(autouse=True)
def seed_store() -> None:
    store.clear()
    assert store.create_merchant(_merchant())
    store.upsert_order(_order("current", 0))
    yield
    store.clear()


def test_purchase_history_qualifies_two_ip_backed_matches_in_window() -> None:
    store.upsert_order(_order("prior_1", 120))
    store.upsert_order(_order("prior_2", 365, user_agent="Other"))
    state = _state()

    result = check_ce3_qualification(state)

    assert result["qualifies"] is True
    assert result["prior_transaction_refs"] == ["prior_1", "prior_2"]
    assert "customer_ip" in result["matched_elements"]
    assert state["disputed_order_ip"] == "203.0.113.10"
    assert state["disputed_order_user_agent"] == "Browser/1.0"


def test_purchase_history_rejects_only_one_matching_candidate() -> None:
    store.upsert_order(_order("prior_1", 200))
    assert check_ce3_qualification(_state())["qualifies"] is False


def test_purchase_history_excludes_orders_outside_date_window() -> None:
    store.upsert_order(_order("too_recent", 119))
    store.upsert_order(_order("too_old", 366))
    assert check_ce3_qualification(_state())["reason"] == "insufficient_history"


def test_purchase_history_excludes_disputed_and_fraud_flagged_candidates() -> None:
    store.upsert_order(_order("disputed", 200, is_disputed=True))
    store.upsert_order(_order("fraud", 250, is_fraud_flagged=True))
    assert check_ce3_qualification(_state())["qualifies"] is False


def test_purchase_history_no_history_returns_false_without_raising() -> None:
    result = check_ce3_qualification(_state())
    assert result == {"qualifies": False, "matched_elements": [], "prior_transaction_refs": [], "reason": "insufficient_history"}
