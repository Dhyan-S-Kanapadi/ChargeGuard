import logging

from api.store import store
from core.state import ChargebackState, OrderRecord


logger = logging.getLogger(__name__)
CORRELATION_UNAVAILABLE = "commerce_order_correlation_unavailable"


def _apply_order(state: ChargebackState, order: OrderRecord, source: str) -> None:
    state["commerce_order_id"] = order["order_id"]
    state["order_id"] = order["order_id"]
    for field in ("commerce_order_number", "tracking_id", "fulfillment_id"):
        value = order.get(field)
        if value:
            state[field] = value
    state["correlation_status"] = "resolved"
    state["correlation_source"] = source
    state.pop("correlation_failure_reason", None)
    store.mark_order_disputed(order["merchant_id"], order["order_id"])


def _mark_unresolved(state: ChargebackState) -> None:
    state["correlation_status"] = "unresolved"
    state["correlation_source"] = "none"
    state["correlation_failure_reason"] = CORRELATION_UNAVAILABLE
    state["evidence_collection_degraded"] = True
    reasons = state.setdefault("degraded_reasons", [])
    if CORRELATION_UNAVAILABLE not in reasons:
        reasons.append(CORRELATION_UNAVAILABLE)


def order_correlation_agent(state: ChargebackState) -> ChargebackState:
    """Resolve provider identifiers to one merchant-scoped commerce order."""
    merchant_id = state["merchant_profile"]["merchant_id"]
    if state.get("provider") != "razorpay" and state.get("order_id"):
        order = store.get_order(merchant_id, state["order_id"])
        if order:
            _apply_order(state, order, "merchant_order_id")
        else:
            state["commerce_order_id"] = state["order_id"]
            state["correlation_status"] = "resolved"
            state["correlation_source"] = "authenticated_merchant_order_id"
        return state

    transaction = state.get("transaction") or {}
    payment_id = str(transaction.get("payment_id") or state.get("payment_id") or "")
    provider_order_id = str(
        transaction.get("provider_order_id")
        or state.get("provider_order_id")
        or ""
    )
    if provider_order_id:
        state["provider_order_id"] = provider_order_id

    order = (
        store.get_order_by_provider_payment_id(merchant_id, payment_id)
        if payment_id
        else None
    )
    source = "provider_payment_id"
    if order is None and provider_order_id:
        order = store.get_order_by_provider_order_id(merchant_id, provider_order_id)
        source = "provider_order_id"
    if order is None:
        commerce_id = str(transaction.get("commerce_order_reference") or "")
        commerce_number = str(
            transaction.get("commerce_order_number_reference") or ""
        )
        if commerce_id:
            order = store.get_order(merchant_id, commerce_id)
        if order is None and commerce_number:
            order = store.get_order_by_commerce_order_number(
                merchant_id,
                commerce_number,
            )
        source = "verified_razorpay_order_reference"

    if order is None:
        logger.info(
            "Commerce order correlation was unavailable",
            extra={"chargeback_id": state["chargeback_id"], "merchant_id": merchant_id},
        )
        _mark_unresolved(state)
        return state
    _apply_order(state, order, source)
    return state
