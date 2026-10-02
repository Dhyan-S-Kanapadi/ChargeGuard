"""Verification and minimal normalization for inbound Stripe dispute webhooks."""

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import time
from typing import Any


class StripeWebhookError(ValueError):
    pass


def verify_signature(raw_body: bytes, header: str | None, secret: str | None = None) -> bool:
    """Verify Stripe's t=...,v1=... signature before decoding the body."""
    if not header:
        return False
    values: dict[str, list[str]] = {}
    for item in header.split(","):
        key, separator, value = item.partition("=")
        if not separator or not key or not value:
            return False
        values.setdefault(key, []).append(value)
    try:
        timestamp = int(values["t"][0])
    except (KeyError, ValueError):
        return False
    try:
        tolerance = max(1, int(os.getenv("STRIPE_WEBHOOK_TOLERANCE_SECONDS", "300")))
    except ValueError:
        tolerance = 300
    if abs(time.time() - timestamp) > tolerance:
        return False
    webhook_secret = secret if secret is not None else os.getenv("STRIPE_WEBHOOK_SECRET", "")
    if not webhook_secret or not values.get("v1"):
        return False
    expected = hmac.new(webhook_secret.encode("utf-8"), str(timestamp).encode("ascii") + b"." + raw_body, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, candidate) for candidate in values["v1"])


def parse_event(raw_body: bytes) -> dict[str, Any]:
    try:
        event = json.loads(raw_body)
        data = event["data"]["object"]
    except (KeyError, TypeError, UnicodeDecodeError, ValueError) as exc:
        raise StripeWebhookError("Webhook body was not a valid Stripe event.") from exc
    if not isinstance(event, dict) or not isinstance(data, dict):
        raise StripeWebhookError("Webhook body was not a valid Stripe event.")
    if not all(isinstance(event.get(key), str) and event[key] for key in ("id", "type")):
        raise StripeWebhookError("Webhook event ID and type are required.")
    return event


def _timestamp(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), timezone.utc) if value is not None else None
    except (TypeError, ValueError, OSError):
        return None


def serialize_event(event: dict[str, Any]) -> dict[str, Any]:
    """Return only lifecycle fields; never persist Stripe's original object."""
    dispute = event["data"]["object"]
    payment_method = dispute.get("payment_method_details") if isinstance(dispute.get("payment_method_details"), dict) else {}
    card = payment_method.get("card") if isinstance(payment_method.get("card"), dict) else {}
    return {
        "id": event["id"], "type": event["type"], "account": event.get("account"),
        "created": event.get("created"),
        "data": {"object": {
            "id": dispute.get("id"), "payment_intent": dispute.get("payment_intent"),
            "charge": dispute.get("charge"), "amount": dispute.get("amount"),
            "currency": dispute.get("currency"), "reason": dispute.get("reason"),
            "status": dispute.get("status"), "created": dispute.get("created"),
            "due_by": (dispute.get("evidence_details") or {}).get("due_by") if isinstance(dispute.get("evidence_details"), dict) else None,
            "payment_method_type": payment_method.get("type"), "network": card.get("network"),
        }},
    }


def event_timestamp(event: dict[str, Any]) -> datetime | None:
    return _timestamp(event.get("created"))
