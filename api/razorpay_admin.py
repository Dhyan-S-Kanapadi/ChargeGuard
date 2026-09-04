"""Protected Razorpay operations for reconciliation and event remediation."""

from collections.abc import Callable
from datetime import datetime, timezone
import logging
import os
from threading import Lock, Thread
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from api import webhooks as internal_webhooks
from api.auth import require_api_key
from api.razorpay_processor import (
    process_razorpay_provider_event,
    safe_razorpay_failure_reason,
)
from api.razorpay_service import process_normalized_dispute
from api.schemas import RazorpayReconciliationRequest
from api.store import store
from integrations.razorpay import (
    RazorpayClient,
    RazorpayConfigError,
    RazorpayRequestError,
)
from integrations.credential_secrets import CredentialStoreError
from integrations.payment_client_factory import (
    PaymentClientFactory,
    PaymentConnectorError,
    global_payment_fallback_enabled,
)
from integrations.razorpay_schemas import RazorpayWebhookEnvelope
from integrations.razorpay_webhook import (
    normalize_dispute,
    serialize_envelope_for_processing,
)


router = APIRouter(
    prefix="/internal/razorpay",
    tags=["razorpay-internal"],
    dependencies=[Depends(require_api_key)],
)
logger = logging.getLogger(__name__)
_MAX_RECOVERY_BATCH_SIZE = 100
_DEFAULT_STARTUP_RECOVERY_LIMIT = 25
_STARTUP_RECOVERY_LOCK = Lock()
_startup_recovery_started = False
payment_client_factory = PaymentClientFactory(store)
_PUBLIC_EVENT_FIELDS = (
    "event_id",
    "provider_event_id",
    "provider",
    "event_type",
    "event_name",
    "provider_dispute_id",
    "chargeback_id",
    "payment_id",
    "account_id",
    "merchant_id",
    "payload_hash",
    "payload_sha256",
    "event_id_source",
    "provider_event_timestamp",
    "processing_state",
    "processing_status",
    "received_at",
    "last_attempt_at",
    "attempt_count",
    "processed_at",
    "failure_reason",
    "error",
)


def _safe_event_response(event: dict[str, Any]) -> dict[str, Any]:
    return {
        key: event[key]
        for key in _PUBLIC_EVENT_FIELDS
        if key in event
    }


def _enqueue_event(background_tasks: BackgroundTasks, event_id: str) -> None:
    background_tasks.add_task(process_razorpay_provider_event, event_id)


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _startup_recovery_limit() -> int:
    try:
        configured = int(
            os.getenv(
                "RAZORPAY_STARTUP_RECOVERY_LIMIT",
                str(_DEFAULT_STARTUP_RECOVERY_LIMIT),
            )
        )
    except ValueError:
        logger.warning(
            "Invalid RAZORPAY_STARTUP_RECOVERY_LIMIT; using default",
            extra={"default_limit": _DEFAULT_STARTUP_RECOVERY_LIMIT},
        )
        configured = _DEFAULT_STARTUP_RECOVERY_LIMIT
    return min(_MAX_RECOVERY_BATCH_SIZE, max(1, configured))


def recover_pending_razorpay_events(
    *,
    limit: int,
    schedule_event: Callable[[str], None],
) -> dict[str, int]:
    """Queue a bounded set of persisted events without exposing their payloads."""
    bounded_limit = min(_MAX_RECOVERY_BATCH_SIZE, max(1, limit))
    events = store.list_recoverable_provider_events(
        provider="razorpay",
        limit=bounded_limit,
    )
    scheduled = 0
    skipped = 0
    failed = 0
    for event in events:
        event_id = str(event["event_id"])
        if event.get("processing_state") == "unresolved" and not (
            event.get("account_id")
            and store.get_merchant_by_razorpay_account_id(event["account_id"])
        ):
            skipped += 1
            continue
        if not store.requeue_provider_event(event_id):
            skipped += 1
            continue
        try:
            schedule_event(event_id)
            scheduled += 1
        except Exception as exc:
            store.update_provider_event(
                event_id,
                processing_state="failed",
                failure_reason=safe_razorpay_failure_reason(exc),
            )
            failed += 1
    return {
        "considered": len(events),
        "scheduled": scheduled,
        "skipped": skipped,
        "failed": failed,
    }


def _startup_recovery_worker(limit: int) -> None:
    result = recover_pending_razorpay_events(
        limit=limit,
        schedule_event=process_razorpay_provider_event,
    )
    logger.info("Razorpay startup recovery completed", extra=result)


def schedule_startup_razorpay_recovery() -> bool:
    """Start one non-blocking recovery worker when startup recovery is enabled."""
    global _startup_recovery_started
    if not _env_flag("RAZORPAY_RECOVER_PENDING_ON_STARTUP", True):
        return False
    with _STARTUP_RECOVERY_LOCK:
        if _startup_recovery_started:
            return False
        _startup_recovery_started = True
    try:
        Thread(
            target=_startup_recovery_worker,
            args=(_startup_recovery_limit(),),
            name="razorpay-startup-recovery",
            daemon=True,
        ).start()
    except Exception:
        with _STARTUP_RECOVERY_LOCK:
            _startup_recovery_started = False
        logger.error("Unable to start Razorpay startup recovery worker")
        return False
    return True


def reset_startup_recovery_state() -> None:
    """Reset process-local startup state for tests."""
    global _startup_recovery_started
    with _STARTUP_RECOVERY_LOCK:
        _startup_recovery_started = False


def _event_for_status(status: str) -> str:
    return {
        "action_required": "payment.dispute.action_required",
        "under_review": "payment.dispute.under_review",
        "won": "payment.dispute.won",
        "lost": "payment.dispute.lost",
        "closed": "payment.dispute.closed",
    }.get(status.lower(), "payment.dispute.created")


def _reconciliation_envelope(
    dispute: dict[str, Any],
    payment: dict[str, Any] | None,
    account_id: str,
) -> RazorpayWebhookEnvelope:
    event_name = _event_for_status(str(dispute.get("status") or "open"))
    timestamp = dispute.get("updated_at") or dispute.get("created_at")
    return RazorpayWebhookEnvelope.model_validate(
        {
            "entity": "event",
            "account_id": account_id,
            "event": event_name,
            "payload": {
                "payment": {"entity": payment} if payment else None,
                "dispute": {"entity": dispute},
            },
            "created_at": timestamp,
        }
    )


@router.get("/events")
def list_razorpay_events(
    processing_state: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    events = [
        event
        for event in store.list_provider_events()
        if event.get("provider") == "razorpay"
    ]
    if processing_state:
        events = [
            event
            for event in events
            if event.get("processing_state") == processing_state
        ]
    return [_safe_event_response(event) for event in events]


@router.post("/events/{event_id}/retry")
def retry_razorpay_event(
    event_id: str,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    event = store.get_provider_event(event_id)
    if event is None or event.get("provider") != "razorpay":
        raise HTTPException(status_code=404, detail="Razorpay event not found.")
    if not store.requeue_provider_event(event_id, include_received=False):
        raise HTTPException(
            status_code=409,
            detail="Razorpay event is not eligible for retry.",
        )
    _enqueue_event(background_tasks, event_id)
    return {"status": "queued", "event_id": event_id}


@router.post("/process-pending")
def process_pending_razorpay_events(
    background_tasks: BackgroundTasks,
    limit: int = Query(default=25, ge=1, le=_MAX_RECOVERY_BATCH_SIZE),
) -> dict[str, int]:
    return recover_pending_razorpay_events(
        limit=limit,
        schedule_event=lambda event_id: _enqueue_event(background_tasks, event_id),
    )


@router.post("/reconcile")
def reconcile_razorpay_disputes(
    payload: RazorpayReconciliationRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    merchant = store.get_merchant(payload.merchant_id)
    if merchant is None:
        raise HTTPException(status_code=404, detail="Merchant not found.")
    connector_id = merchant.get("payment_connector_ids", {}).get("razorpay")
    connector = (
        store.get_payment_connector(payload.merchant_id, connector_id)
        if connector_id
        else None
    )
    account_id = (
        connector.get("provider_account_id")
        if connector and connector.get("status") == "verified"
        else merchant.get("razorpay_account_id")
        if not connector_id and global_payment_fallback_enabled()
        else None
    )
    if not account_id:
        raise HTTPException(
            status_code=422,
            detail="Merchant must have a verified Razorpay payment connector.",
        )

    try:
        client = payment_client_factory.for_merchant(merchant, "razorpay")
        disputes = client.list_disputes(
            from_timestamp=payload.from_timestamp,
            to_timestamp=payload.to_timestamp,
            count=payload.count,
            skip=payload.skip,
        )
    except (
        CredentialStoreError,
        PaymentConnectorError,
        RazorpayConfigError,
        RazorpayRequestError,
    ) as exc:
        detail = (
            exc.code
            if isinstance(exc, (CredentialStoreError, PaymentConnectorError))
            else "razorpay_reconciliation_unavailable"
        )
        raise HTTPException(status_code=503, detail=detail) from exc
    results: list[dict[str, Any]] = []
    for dispute in disputes:
        dispute_id = str(dispute.get("id") or "")
        payment_id = str(dispute.get("payment_id") or "")
        if not dispute_id or not payment_id:
            results.append({"status": "invalid", "provider_dispute_id": dispute_id or None})
            continue
        payment = None
        enrichment_failure = None
        try:
            payment = client.get_payment(payment_id, expand_card=True)
        except RazorpayRequestError as exc:
            enrichment_failure = str(exc)
        event_name = _event_for_status(str(dispute.get("status") or "open"))
        event_version = dispute.get("updated_at") or dispute.get("created_at") or 0
        event_id = f"reconcile:{dispute_id}:{event_name}:{event_version}"
        try:
            envelope = _reconciliation_envelope(dispute, payment, account_id)
            event_data = serialize_envelope_for_processing(envelope)
        except Exception:
            results.append({"status": "invalid", "provider_dispute_id": dispute_id})
            continue
        claimed = store.claim_provider_event(
            {
                "event_id": event_id,
                "provider": "razorpay",
                "event_type": event_name,
                "provider_dispute_id": dispute_id,
                "payment_id": payment_id,
                "account_id": account_id,
                "payload_hash": None,
                "event_id_source": "reconciliation",
                "provider_event_timestamp": envelope.created_at,
                "event_data": event_data,
                "processing_state": "processing",
                "received_at": datetime.now(timezone.utc),
            }
        )
        if not claimed:
            results.append({"status": "duplicate", "provider_dispute_id": dispute_id})
            continue
        try:
            normalized = normalize_dispute(
                envelope,
                webhook_event_id=event_id,
                enriched_payment=payment,
                enrichment_failure_reason=enrichment_failure,
            )
            result = process_normalized_dispute(
                normalized,
                merchant,
                lambda state: background_tasks.add_task(
                    internal_webhooks.run_chargeback_graph,
                    state,
                ),
            )
        except Exception as exc:
            store.update_provider_event(
                event_id,
                processing_state="failed",
                failure_reason=safe_razorpay_failure_reason(exc),
            )
            results.append({"status": "failed", "provider_dispute_id": dispute_id})
            continue
        store.update_provider_event(
            event_id,
            processing_state=result["status"],
            merchant_id=merchant["merchant_id"],
        )
        results.append(result)
    return {"count": len(results), "results": results}
