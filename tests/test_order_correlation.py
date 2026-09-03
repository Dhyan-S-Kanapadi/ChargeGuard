from datetime import datetime, timedelta, timezone
from typing import cast

import pytest

from agents.evidence.order_correlation import order_correlation_agent
from agents.evidence.purchase_history import check_ce3_qualification
from agents.evidence.shipping import shipping_agent
from agents.scoring import scoring_agent
from api.store import store
from core.state import ChargebackState, MerchantProfile, OrderRecord


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _merchant(merchant_id: str = "merchant_correlation") -> MerchantProfile:
    return {
        "merchant_id": merchant_id,
        "name": "Correlation Merchant",
        "vertical": "ecommerce",
        "payment_provider": "razorpay",
        "freshdesk_domain": "",
        "average_order_value": 0,
        "chargeback_history_count": 0,
    }


def _order(order_id: str, **updates) -> OrderRecord:
    order: OrderRecord = {
        "order_id": order_id,
        "merchant_id": "merchant_correlation",
        "customer_email": "buyer@example.com",
        "customer_ip": "203.0.113.10",
        "user_agent": "Browser/1.0",
        "shipping_address": {"address1": "1 Main Street"},
        "order_date": NOW,
        "is_disputed": False,
        "is_fraud_flagged": False,
    }
    order.update(updates)
    return order


def _state(**updates) -> ChargebackState:
    state = cast(
        ChargebackState,
        {
            "chargeback_id": "disp_correlation",
            "payment_id": "pay_rzp_001",
            "provider_order_id": "order_rzp_001",
            "provider": "razorpay",
            "reason_code": "10.4",
            "card_network": "VISA",
            "dispute_amount": 2500.0,
            "currency": "INR",
            "filing_deadline": NOW + timedelta(days=30),
            "merchant_profile": _merchant(),
            "investigation_plan": {},
            "requires_food_agents": False,
            "transaction": {
                "order_id": "order_rzp_001",
                "provider_order_id": "order_rzp_001",
                "payment_id": "pay_rzp_001",
                "customer_email": "buyer@example.com",
            },
            "shipping": None,
            "comms": None,
            "device": None,
            "consortium": None,
            "delivery_photo": None,
            "order_timeline": None,
            "evidence_collection_degraded": False,
            "degraded_reasons": [],
            "decision": None,
        },
    )
    state.update(updates)
    return state


@pytest.fixture(autouse=True)
def clear_store() -> None:
    store.clear()
    assert store.create_merchant(_merchant())
    yield
    store.clear()


def test_exact_payment_id_resolves_different_razorpay_and_shopify_ids() -> None:
    store.upsert_order(
        _order(
            "shopify_9001",
            provider_payment_id="pay_rzp_001",
            provider_order_id="order_rzp_other",
            commerce_order_number="#9001",
            tracking_id="awb_9001",
            fulfillment_id="fulfillment_9001",
        )
    )

    result = order_correlation_agent(_state())

    assert result["provider_order_id"] == "order_rzp_001"
    assert result["commerce_order_id"] == "shopify_9001"
    assert result["order_id"] == "shopify_9001"
    assert result["commerce_order_number"] == "#9001"
    assert result["tracking_id"] == "awb_9001"
    assert result["fulfillment_id"] == "fulfillment_9001"
    assert result["correlation_source"] == "provider_payment_id"
    assert store.get_order("merchant_correlation", "shopify_9001")["is_disputed"] is True


def test_exact_provider_order_id_is_second_priority() -> None:
    store.upsert_order(
        _order("shopify_9002", provider_order_id="order_rzp_001")
    )
    state = _state(payment_id="pay_unmapped")
    state["transaction"]["payment_id"] = "pay_unmapped"

    result = order_correlation_agent(state)

    assert result["commerce_order_id"] == "shopify_9002"
    assert result["correlation_source"] == "provider_order_id"


def test_verified_razorpay_order_reference_is_third_priority() -> None:
    store.upsert_order(_order("shopify_9003", commerce_order_number="#9003"))
    state = _state(payment_id="pay_unmapped", provider_order_id="order_unmapped")
    state["transaction"].update(
        {
            "payment_id": "pay_unmapped",
            "provider_order_id": "order_unmapped",
            "commerce_order_number_reference": "#9003",
        }
    )

    result = order_correlation_agent(state)

    assert result["commerce_order_id"] == "shopify_9003"
    assert result["correlation_source"] == "verified_razorpay_order_reference"


def test_resolver_never_fuzzy_matches_customer_data_and_forces_escalation(monkeypatch) -> None:
    store.upsert_order(_order("same_email_but_unmapped"))
    state = order_correlation_agent(_state())
    monkeypatch.setattr(
        "agents.scoring._predict_win_probability",
        lambda _: (0.99, "logistic_regression"),
    )

    result = scoring_agent(state)

    assert state["correlation_status"] == "unresolved"
    assert "commerce_order_correlation_unavailable" in state["degraded_reasons"]
    assert result["decision"] == "ESCALATE_DEGRADED"


def test_resolver_rejects_cross_merchant_identifier_match() -> None:
    other = _merchant("merchant_other")
    assert store.create_merchant(other)
    cross_merchant_order = _order(
        "shopify_other",
        merchant_id="merchant_other",
        provider_payment_id="pay_rzp_001",
    )
    store.upsert_order(cross_merchant_order)

    result = order_correlation_agent(_state())

    assert result["correlation_status"] == "unresolved"
    assert "commerce_order_id" not in result


def test_resolved_tracking_id_is_used_by_shipping(monkeypatch) -> None:
    store.upsert_order(
        _order("shopify_9004", provider_payment_id="pay_rzp_001", tracking_id="awb_9004")
    )
    state = order_correlation_agent(_state())
    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "true")

    result = shipping_agent(state)

    assert result["shipping"]["tracking_id"] == "awb_9004"


def test_ce3_uses_resolved_commerce_order_when_provider_id_differs() -> None:
    store.upsert_order(
        _order(
            "shopify_current",
            provider_payment_id="pay_rzp_001",
            order_date=NOW,
        )
    )
    store.upsert_order(_order("shopify_prior_1", order_date=NOW - timedelta(days=150)))
    store.upsert_order(_order("shopify_prior_2", order_date=NOW - timedelta(days=300)))

    state = order_correlation_agent(_state())
    result = check_ce3_qualification(state)

    assert state["commerce_order_id"] == "shopify_current"
    assert result["qualifies"] is True
    assert result["prior_transaction_refs"] == ["shopify_prior_1", "shopify_prior_2"]
