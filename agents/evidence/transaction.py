import logging
import os
from typing import Any

from core.state import ChargebackState, TransactionEvidence
from integrations.razorpay import RazorpayClient
from integrations.stripe import StripeClient


logger = logging.getLogger(__name__)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _payment_provider(state: ChargebackState) -> str:
    configured = state["merchant_profile"].get("payment_provider")
    if configured:
        return configured
    return "razorpay" if state["currency"].upper() == "INR" else "stripe"


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
    *,
    source: str = "transaction_agent_stub",
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
            "source": source,
            "payment": payment,
            "order": order,
        },
    }


def _collect_razorpay(state: ChargebackState) -> tuple[dict[str, Any], dict[str, Any]]:
    payment_id = state.get("payment_id")
    order_id = state.get("order_id")
    if not payment_id or not order_id:
        raise ValueError("Razorpay collection requires payment_id and order_id")

    client = RazorpayClient.from_env()
    return client.get_payment(payment_id), client.get_order(order_id)


def _collect_stripe(state: ChargebackState) -> tuple[dict[str, Any], dict[str, Any]]:
    payment_id = state.get("payment_id")
    if not payment_id:
        raise ValueError("Stripe collection requires payment_id")

    client = StripeClient.from_env()
    payment_intent = client.get_payment_intent(payment_id)
    latest_charge_id = payment_intent.get("latest_charge")
    charge = client.get_charge(latest_charge_id) if isinstance(latest_charge_id, str) else {}
    metadata = payment_intent.get("metadata") or {}
    charge_details = charge.get("payment_method_details") or {}
    card_details = charge_details.get("card") or {}
    three_ds = card_details.get("three_d_secure") or {}
    three_ds_result = str(three_ds.get("result") or "").lower()

    payment = {
        "id": payment_intent.get("id") or payment_id,
        "order_id": metadata.get("order_id") or state.get("order_id", ""),
        "amount": payment_intent.get("amount"),
        "currency": str(payment_intent.get("currency") or state["currency"]).upper(),
        "customer_email": payment_intent.get("receipt_email") or charge.get("receipt_email"),
        "metadata": metadata,
        "three_ds_authenticated": three_ds_result in {
            "authenticated",
            "attempt_acknowledged",
        },
        "otp_verified": False,
        "provider_raw": {
            "payment_intent": payment_intent,
            "charge": charge,
        },
    }
    order = {
        "id": payment["order_id"],
        "status": payment_intent.get("status"),
        "customer": {
            "email": payment["customer_email"],
            "order_history_count": metadata.get("order_history_count", 0),
            "previous_chargebacks": metadata.get("previous_chargebacks", 0),
        },
    }
    return payment, order


def _collect_transaction_data(
    state: ChargebackState,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    if _env_flag("CHARGEGUARD_USE_STUBS"):
        return _stub_payment_response(state), _stub_order_response(state), "transaction_agent_stub"

    provider = _payment_provider(state)
    if provider == "razorpay":
        payment, order = _collect_razorpay(state)
    elif provider == "stripe":
        payment, order = _collect_stripe(state)
    else:
        raise ValueError(f"Unsupported payment provider: {provider}")
    return payment, order, provider


def transaction_agent(state: ChargebackState) -> ChargebackState:
    logger.info("Running transaction agent")

    try:
        payment, order, source = _collect_transaction_data(state)
        state["transaction"] = _build_transaction_evidence(
            state,
            payment,
            order,
            source=source,
        )
    except Exception as exc:
        logger.exception("Transaction evidence collection failed")
        state["transaction"] = _empty_transaction_evidence(state, error=str(exc))

    return state
