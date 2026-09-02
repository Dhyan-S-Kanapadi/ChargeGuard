"""Signed Razorpay dispute webhook receiver."""

import os
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from fastapi.responses import JSONResponse

from api.razorpay_processor import process_razorpay_provider_event
from api.store import store
from integrations.razorpay_webhook import (
    RazorpayWebhookError,
    parse_envelope,
    parse_event_header,
    payload_sha256,
    serialize_envelope_for_processing,
    verify_signature,
)


router = APIRouter(prefix="/webhook", tags=["razorpay-webhooks"])
SUPPORTED_EVENTS = {
    "payment.dispute.created",
    "payment.dispute.action_required",
    "payment.dispute.under_review",
    "payment.dispute.won",
    "payment.dispute.lost",
    "payment.dispute.closed",
}


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _max_body_bytes() -> int:
    try:
        return max(1024, int(os.getenv("RAZORPAY_WEBHOOK_MAX_BODY_BYTES", "1048576")))
    except ValueError:
        return 1048576


def _initial_event_record(
    *,
    event_id: str,
    event_type: str,
    account_id: str,
    payload_hash: str,
    event_id_source: str,
    event_data: dict[str, Any],
    provider_dispute_id: str,
    payment_id: str,
    provider_event_timestamp: int | None,
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "provider": "razorpay",
        "event_type": event_type,
        "provider_dispute_id": provider_dispute_id,
        "chargeback_id": provider_dispute_id,
        "payment_id": payment_id,
        "account_id": account_id,
        "payload_hash": payload_hash,
        "payload_sha256": payload_hash,
        "event_id_source": event_id_source,
        "provider_event_timestamp": provider_event_timestamp,
        "event_data": event_data,
        "processing_state": "received",
    }


def enqueue_razorpay_provider_event(
    background_tasks: BackgroundTasks,
    event_id: str,
) -> None:
    """Schedule deferred work only after the event has been persisted."""
    background_tasks.add_task(process_razorpay_provider_event, event_id)


@router.post("/razorpay")
async def receive_razorpay_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    if not _env_flag("RAZORPAY_WEBHOOK_ENABLED", True):
        raise HTTPException(status_code=404, detail="Razorpay webhook is disabled.")

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _max_body_bytes():
                raise HTTPException(status_code=413, detail="Razorpay webhook body is too large.")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length header.")
    raw_body = await request.body()
    if len(raw_body) > _max_body_bytes():
        raise HTTPException(status_code=413, detail="Razorpay webhook body is too large.")

    signature = request.headers.get("X-Razorpay-Signature")
    if not verify_signature(raw_body, signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Razorpay signature.",
        )

    digest = payload_sha256(raw_body)
    header_event_id = request.headers.get("x-razorpay-event-id")
    event_id = header_event_id or f"sha256:{digest}"
    try:
        header = parse_event_header(raw_body)
        envelope = parse_envelope(raw_body)
    except RazorpayWebhookError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    dispute = envelope.payload.dispute.entity

    claimed = store.claim_provider_event(
        _initial_event_record(
            event_id=event_id,
            event_type=header.event,
            account_id=header.account_id,
            payload_hash=digest,
            event_id_source="header" if header_event_id else "payload_hash",
            event_data=serialize_envelope_for_processing(envelope),
            provider_dispute_id=dispute.id,
            payment_id=dispute.payment_id,
            provider_event_timestamp=header.created_at,
        )
    )
    if not claimed:
        return {"status": "duplicate", "event_id": event_id}

    if header.event not in SUPPORTED_EVENTS:
        store.update_provider_event(event_id, processing_state="ignored")
        return {"status": "ignored", "event_id": event_id}

    if not store.queue_provider_event(event_id):
        return {"status": "duplicate", "event_id": event_id}
    enqueue_razorpay_provider_event(background_tasks, event_id)
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "queued",
            "event_id": event_id,
            "provider_dispute_id": dispute.id,
        },
    )
