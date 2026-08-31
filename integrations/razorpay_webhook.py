"""Verification and safe extraction for inbound Razorpay dispute webhooks."""

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from typing import Any


class RazorpayWebhookError(ValueError):
    """Raised when a signed Razorpay event is structurally unusable."""


def verify_signature(raw_body: bytes, signature: str | None, secret: str | None = None) -> bool:
    if not signature:
        return False
    webhook_secret = secret if secret is not None else os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
    if not webhook_secret:
        return False
    expected = hmac.new(webhook_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def parse_envelope(raw_body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise RazorpayWebhookError("Webhook body was not valid JSON.") from exc
    if not isinstance(payload, dict) or payload.get("entity") != "event":
        raise RazorpayWebhookError("Webhook body was not a Razorpay event envelope.")
    if not isinstance(payload.get("payload"), dict):
        raise RazorpayWebhookError("Webhook event did not include payload.")
    return payload


def entity(envelope: dict[str, Any], name: str) -> dict[str, Any]:
    value = envelope.get("payload", {}).get(name, {})
    result = value.get("entity") if isinstance(value, dict) else None
    return result if isinstance(result, dict) else {}


def utc_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError) as exc:
        raise RazorpayWebhookError("Invalid Razorpay timestamp.") from exc


def mapped_values(envelope: dict[str, Any]) -> dict[str, Any]:
    payment = entity(envelope, "payment")
    dispute = entity(envelope, "dispute")
    notes = payment.get("notes") if isinstance(payment.get("notes"), dict) else {}
    amount = dispute.get("amount")
    if not dispute.get("id") or not isinstance(amount, int) or amount <= 0:
        raise RazorpayWebhookError("Webhook dispute is missing required id or amount.")
    return {
        "chargeback_id": str(dispute["id"]),
        "payment_id": str(dispute.get("payment_id") or payment.get("id") or ""),
        "order_id": str(payment.get("order_id") or ""),
        "dispute_amount": amount / 100,
        "currency": str(dispute.get("currency") or payment.get("currency") or "").upper(),
        "filing_deadline": utc_timestamp(dispute.get("respond_by")),
        "card_network": notes.get("chargeguard_card_network"),
        "network_reason_code": notes.get("chargeguard_network_reason_code"),
        "provider_reason_code": dispute.get("reason_code"),
        "provider_dispute_status": dispute.get("status"),
        "provider_phase": dispute.get("phase"),
        "provider_respond_by": utc_timestamp(dispute.get("respond_by")),
    }


def payload_sha256(raw_body: bytes) -> str:
    return hashlib.sha256(raw_body).hexdigest()
