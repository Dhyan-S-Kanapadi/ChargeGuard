import logging
from typing import Any

from core.state import ChargebackState, TransactionEvidence


logger = logging.getLogger(__name__)


def _bool_from_any(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "verified", "authenticated"}
    return bool(value)


def _minor_units_to_amount(value: Any, fallback: float) -> float:
    if value is None:
        return fallback

    try:
        return float(value) / 100
    except (TypeError, ValueError):
        logger.warning("Unable to parse payment amount %r, falling back to dispute amount", value)
        return fallback


def _extract_customer(order: dict[str, Any], payment: dict[str, Any]) -> dict[str, Any]:
    customer = order.get("customer") or {}
    if customer:
        return customer

    return {
        "email": payment.get("email") or payment.get("customer_email"),
        "order_history_count": order.get("order_history_count"),
        "previous_chargebacks": order.get("previous_chargebacks"),
    }


def _extract_3ds_authenticated(payment: dict[str, Any]) -> bool:
    card = payment.get("card") or {}
    acquirer_data = payment.get("acquirer_data") or {}
    authentication = payment.get("authentication") or {}
    three_ds = authentication.get("three_ds") or {}

    return any(
        (
            _bool_from_any(payment.get("three_ds_authenticated")),
            _bool_from_any(three_ds.get("authenticated")),
            bool(card.get("authentication_reference_number")),
            bool(acquirer_data.get("authentication_reference_number")),
        )
    )


def _extract_otp_verified(payment: dict[str, Any]) -> bool:
    notes = payment.get("notes") or {}
    authentication = payment.get("authentication") or {}

    return any(
        (
            _bool_from_any(payment.get("otp_verified")),
            _bool_from_any(notes.get("otp_verified")),
            _bool_from_any(authentication.get("otp_verified")),
        )
    )


def _empty_transaction_evidence(
    state: ChargebackState,
    *,
    error: str | None = None,
) -> TransactionEvidence:
    return {
        "order_id": state.get("order_id", ""),
        "payment_id": state.get("payment_id", ""),
        "amount": 0.0,
        "currency": state.get("currency", "INR"),
        "otp_verified": False,
        "three_ds_authenticated": False,
        "device_id": "",
        "ip_address": "",
        "customer_email": "",
        "order_history_count": 0,
        "previous_chargebacks": 0,
        "raw": {
            "source": "transaction_agent_empty",
            "error": error,
        },
    }


def _stub_payment_response(state: ChargebackState) -> dict[str, Any]:
    payment_id = state.get("payment_id") or f"pay_{state['chargeback_id']}"
    order_id = state.get("order_id") or f"order_{state['chargeback_id']}"

    return {
        "id": payment_id,
        "order_id": order_id,
        "amount": int(state["dispute_amount"] * 100),
        "currency": state["currency"],
        "email": "customer@example.com",
        "method": "card",
        "card": {
            "authentication_reference_number": "auth_stub_001",
            "network": state["card_network"],
        },
        "acquirer_data": {
            "auth_code": "123456",
        },
        "notes": {
            "device_id": "device_demo_123",
            "ip_address": "49.36.18.22",
        },
        "three_ds_authenticated": True,
        "otp_verified": True,
    }


def _stub_order_response(state: ChargebackState) -> dict[str, Any]:
    return {
        "id": state.get("order_id") or f"order_{state['chargeback_id']}",
        "receipt": f"receipt_{state['chargeback_id']}",
        "status": "paid",
        "customer": {
            "email": "customer@example.com",
            "order_history_count": 8,
            "previous_chargebacks": 0,
        },
    }


def _build_transaction_evidence(
    state: ChargebackState,
    payment: dict[str, Any],
    order: dict[str, Any],
) -> TransactionEvidence:
    notes = payment.get("notes") or {}
    metadata = payment.get("metadata") or {}
    customer = _extract_customer(order, payment)
    amount = _minor_units_to_amount(payment.get("amount"), state["dispute_amount"])

    return {
        "order_id": str(payment.get("order_id") or order.get("id") or state.get("order_id", "")),
        "payment_id": str(payment.get("id") or state.get("payment_id", "")),
        "amount": amount,
        "currency": str(payment.get("currency") or state["currency"]),
        "otp_verified": _extract_otp_verified(payment),
        "three_ds_authenticated": _extract_3ds_authenticated(payment),
        "device_id": str(notes.get("device_id") or metadata.get("device_id") or ""),
        "ip_address": str(notes.get("ip_address") or metadata.get("ip_address") or ""),
        "customer_email": str(payment.get("email") or customer.get("email") or ""),
        "order_history_count": int(customer.get("order_history_count") or 0),
        "previous_chargebacks": int(customer.get("previous_chargebacks") or 0),
        "raw": {
            "source": "transaction_agent_stub",
            "payment": payment,
            "order": order,
        },
    }


def transaction_agent(state: ChargebackState) -> ChargebackState:
    logger.info("Running transaction agent")

    try:
        payment = _stub_payment_response(state)
        order = _stub_order_response(state)
        state["transaction"] = _build_transaction_evidence(state, payment, order)
    except Exception as exc:
        logger.exception("Transaction evidence collection failed")
        state["transaction"] = _empty_transaction_evidence(state, error=str(exc))

    return state
