import logging
from typing import Any

from core.state import ChargebackState, TransactionEvidence


logger = logging.getLogger(__name__)


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
    customer = order.get("customer") or {}

    amount_minor_units = payment.get("amount", 0)
    amount = float(amount_minor_units) / 100 if amount_minor_units else state["dispute_amount"]

    return {
        "order_id": str(payment.get("order_id") or order.get("id") or state.get("order_id", "")),
        "payment_id": str(payment.get("id") or state.get("payment_id", "")),
        "amount": amount,
        "currency": str(payment.get("currency") or state["currency"]),
        "otp_verified": bool(payment.get("otp_verified")),
        "three_ds_authenticated": bool(payment.get("three_ds_authenticated")),
        "device_id": str(notes.get("device_id") or ""),
        "ip_address": str(notes.get("ip_address") or ""),
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
