"""Verification and normalization for inbound Razorpay dispute webhooks."""

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import hmac
import os
from typing import Any

from pydantic import ValidationError

from integrations.razorpay_schemas import (
    NormalizedRazorpayDispute,
    RazorpayEventHeader,
    RazorpayPaymentEntity,
    RazorpayWebhookEnvelope,
)


class RazorpayWebhookError(ValueError):
    """Raised when a signed Razorpay event is structurally unusable."""


_SAFE_SIMULATOR_NOTE_KEYS = {
    "chargeguard_simulator",
    "chargeguard_card_network",
    "chargeguard_network_reason_code",
}


def verify_signature(
    raw_body: bytes,
    signature: str | None,
    secret: str | None = None,
) -> bool:
    if not signature:
        return False
    webhook_secret = secret if secret is not None else os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
    if not webhook_secret:
        return False
    expected = hmac.new(
        webhook_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def parse_envelope(raw_body: bytes) -> RazorpayWebhookEnvelope:
    try:
        return RazorpayWebhookEnvelope.model_validate_json(raw_body)
    except ValidationError as exc:
        raise RazorpayWebhookError(
            "Webhook body was not a valid Razorpay dispute event."
        ) from exc


def parse_event_header(raw_body: bytes) -> RazorpayEventHeader:
    try:
        return RazorpayEventHeader.model_validate_json(raw_body)
    except ValidationError as exc:
        raise RazorpayWebhookError("Webhook body was not a valid Razorpay event.") from exc


def serialize_envelope_for_processing(
    envelope: RazorpayWebhookEnvelope,
) -> dict[str, Any]:
    """Return the minimum PII-free event data needed for deferred processing."""
    payment = envelope.payload.payment.entity if envelope.payload.payment else None
    safe_payment = None
    if payment is not None:
        safe_notes = {
            key: payment.notes[key]
            for key in _SAFE_SIMULATOR_NOTE_KEYS
            if key in payment.notes
        }
        safe_payment = {
            "entity": {
                "id": payment.id,
                "amount": payment.amount,
                "currency": payment.currency,
                "order_id": payment.order_id,
                "method": payment.method,
                "card": (
                    {"network": payment.card.network}
                    if payment.card is not None
                    else None
                ),
                "notes": safe_notes,
                "created_at": payment.created_at,
            }
        }

    dispute = envelope.payload.dispute.entity
    return {
        "entity": "event",
        "account_id": envelope.account_id,
        "event": envelope.event,
        "payload": {
            "payment": safe_payment,
            "dispute": {
                "entity": {
                    "id": dispute.id,
                    "payment_id": dispute.payment_id,
                    "amount": dispute.amount,
                    "currency": dispute.currency,
                    "reason_code": dispute.reason_code,
                    "respond_by": dispute.respond_by,
                    "status": dispute.status,
                    "phase": dispute.phase,
                    "created_at": dispute.created_at,
                }
            },
        },
        "created_at": envelope.created_at,
    }


def parse_stored_envelope(data: Any) -> RazorpayWebhookEnvelope:
    """Validate the sanitized event representation loaded from durable storage."""
    try:
        return RazorpayWebhookEnvelope.model_validate(data)
    except ValidationError as exc:
        raise RazorpayWebhookError(
            "Stored Razorpay event data was invalid."
        ) from exc


def utc_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError) as exc:
        raise RazorpayWebhookError("Invalid Razorpay timestamp.") from exc


def payload_sha256(raw_body: bytes) -> str:
    return hashlib.sha256(raw_body).hexdigest()


def _payment_rail(method: str | None) -> str | None:
    if not method:
        return None
    return {
        "card": "CARD",
        "upi": "UPI",
        "netbanking": "NETBANKING",
        "wallet": "WALLET",
        "emi": "EMI",
        "paylater": "PAYLATER",
    }.get(method.strip().lower(), method.strip().upper())


def _card_network(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.replace(" ", "").replace("-", "").upper()
    return {
        "VISA": "VISA",
        "MASTERCARD": "MASTERCARD",
        "MAESTRO": "MASTERCARD",
        "RUPAY": "RUPAY",
        "AMEX": "AMEX",
        "AMERICANEXPRESS": "AMEX",
    }.get(normalized)


def _merged_payment(
    envelope: RazorpayWebhookEnvelope,
    enriched_payment: dict[str, Any] | None,
) -> RazorpayPaymentEntity | None:
    webhook_payment = envelope.payload.payment.entity if envelope.payload.payment else None
    if enriched_payment is None:
        return webhook_payment
    merged = dict(enriched_payment)
    if webhook_payment is not None:
        webhook_values = webhook_payment.model_dump(exclude_none=True)
        merged.update(webhook_values)
        if enriched_payment.get("card") and not webhook_values.get("card"):
            merged["card"] = enriched_payment["card"]
    try:
        return RazorpayPaymentEntity.model_validate(merged)
    except ValidationError as exc:
        raise RazorpayWebhookError("Razorpay payment enrichment was invalid.") from exc


def normalize_dispute(
    envelope: RazorpayWebhookEnvelope,
    *,
    webhook_event_id: str,
    enriched_payment: dict[str, Any] | None = None,
    enrichment_failure_reason: str | None = None,
    allow_simulator_metadata: bool = False,
    now: datetime | None = None,
) -> NormalizedRazorpayDispute:
    dispute = envelope.payload.dispute.entity
    payment = _merged_payment(envelope, enriched_payment)
    deadline = utc_timestamp(dispute.respond_by)
    event_timestamp = utc_timestamp(envelope.created_at)
    current_time = now or datetime.now(timezone.utc)
    rail = _payment_rail(payment.method if payment else None)
    network = _card_network(
        payment.card.network if payment and payment.card is not None else None
    )
    network_reason_code = None
    if allow_simulator_metadata and payment is not None:
        network = network or _card_network(
            str(payment.notes.get("chargeguard_card_network") or "")
        )
        network_reason_code = str(
            payment.notes.get("chargeguard_network_reason_code") or ""
        ) or None
    if rail != "CARD":
        network = None

    return NormalizedRazorpayDispute(
        provider_dispute_id=dispute.id,
        chargeback_id=dispute.id,
        payment_id=dispute.payment_id,
        order_id=payment.order_id if payment else None,
        provider_order_id=payment.order_id if payment else None,
        dispute_amount=Decimal(dispute.amount) / Decimal("100"),
        currency=dispute.currency.upper(),
        filing_deadline=deadline,
        deadline_overdue=deadline is not None and deadline <= current_time,
        provider_reason_code=dispute.reason_code,
        network_reason_code=network_reason_code,
        payment_rail=rail,
        card_network=network,
        provider_status=dispute.status,
        provider_phase=dispute.phase,
        provider_event=envelope.event,
        provider_account_id=envelope.account_id,
        webhook_event_id=webhook_event_id,
        provider_event_timestamp=event_timestamp,
        enrichment_degraded=enrichment_failure_reason is not None,
        enrichment_failure_reason=enrichment_failure_reason,
    )
