"""Recoverable post-ack processing for persisted Razorpay provider events."""

import logging
from collections.abc import Callable
from typing import Any

from api import webhooks as internal_webhooks
from api.razorpay_service import normalize_with_enrichment, process_normalized_dispute
from api.store import store
from core.state import ChargebackState
from integrations.razorpay import RazorpayClient
from integrations.razorpay_webhook import parse_stored_envelope


logger = logging.getLogger(__name__)


class ChargebackGraphExecutionError(RuntimeError):
    """Raised when the persisted dispute workflow reports a failed execution."""


def safe_razorpay_failure_reason(exc: Exception) -> str:
    """Return an operator-safe category without provider payloads or credentials."""
    return f"Razorpay provider event processing failed ({type(exc).__name__})."


def run_provider_chargeback_graph(state: ChargebackState) -> None:
    """Run the graph while allowing the provider event to observe failures."""
    if internal_webhooks.run_chargeback_graph(state) is False:
        raise ChargebackGraphExecutionError("Chargeback graph execution failed.")


def process_razorpay_provider_event(
    event_id: str,
    *,
    client_factory: Callable[[], RazorpayClient] | None = None,
    schedule_graph: Callable[[ChargebackState], None] | None = None,
) -> dict[str, Any]:
    """Process one persisted event after the webhook response has been accepted."""
    event = store.get_provider_event(event_id)
    if event is None:
        return {"status": "missing", "event_id": event_id}
    if not store.start_provider_event_processing(event_id):
        current = store.get_provider_event(event_id)
        return {
            "status": "skipped",
            "event_id": event_id,
            "processing_state": current.get("processing_state") if current else None,
        }

    try:
        event = store.get_provider_event(event_id)
        if event is None:
            raise RuntimeError("Persisted event disappeared during processing.")
        envelope = parse_stored_envelope(event.get("event_data"))
        merchant = store.get_merchant_by_razorpay_account_id(envelope.account_id)
        if merchant is None:
            store.update_provider_event(
                event_id,
                processing_state="unresolved",
                failure_reason="No merchant mapping for Razorpay account ID.",
            )
            return {"status": "unresolved", "event_id": event_id}

        normalized = normalize_with_enrichment(
            envelope,
            event_id,
            merchant,
            client_factory=client_factory,
        )
        result = process_normalized_dispute(
            normalized,
            merchant,
            schedule_graph or run_provider_chargeback_graph,
        )
        store.update_provider_event(
            event_id,
            processing_state=result["status"],
            merchant_id=merchant["merchant_id"],
        )
        return {"event_id": event_id, **result}
    except Exception as exc:
        failure_reason = safe_razorpay_failure_reason(exc)
        logger.error(
            "Razorpay provider event processing failed",
            extra={
                "provider": "razorpay",
                "provider_event_id": event_id,
                "failure_type": type(exc).__name__,
            },
        )
        store.update_provider_event(
            event_id,
            processing_state="failed",
            failure_reason=failure_reason,
        )
        return {"status": "failed", "event_id": event_id}
