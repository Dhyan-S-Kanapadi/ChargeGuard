"""Signed Razorpay dispute webhook receiver."""

import os
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from fastapi.responses import JSONResponse

from api import webhooks as internal_webhooks
from api.razorpay_service import normalize_with_enrichment, process_normalized_dispute
from api.store import store
from integrations.razorpay_webhook import (
    RazorpayWebhookError,
    parse_envelope,
    parse_event_header,
    payload_sha256,
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
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "provider": "razorpay",
        "event_type": event_type,
        "provider_dispute_id": None,
        "account_id": account_id,
        "payload_hash": payload_hash,
        "payload_sha256": payload_hash,
        "event_id_source": event_id_source,
        "processing_state": "received",
    }


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
    except RazorpayWebhookError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    claimed = store.claim_provider_event(
        _initial_event_record(
            event_id=event_id,
            event_type=header.event,
            account_id=header.account_id,
            payload_hash=digest,
            event_id_source="header" if header_event_id else "payload_hash",
        )
    )
    if not claimed:
        return {"status": "duplicate", "event_id": event_id}

    if header.event not in SUPPORTED_EVENTS:
        store.update_provider_event(event_id, processing_state="ignored")
        return {"status": "ignored", "event_id": event_id}

    try:
        envelope = parse_envelope(raw_body)
    except RazorpayWebhookError as exc:
        store.update_provider_event(
            event_id,
            processing_state="failed",
            failure_reason=str(exc),
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    dispute = envelope.payload.dispute.entity
    store.update_provider_event(
        event_id,
        provider_dispute_id=dispute.id,
        chargeback_id=dispute.id,
        payment_id=dispute.payment_id,
        provider_event_timestamp=header.created_at,
        processing_state="processing",
    )

    merchant = store.get_merchant_by_razorpay_account_id(envelope.account_id)
    if merchant is None:
        store.update_provider_event(
            event_id,
            processing_state="unresolved",
            failure_reason="No merchant mapping for Razorpay account ID.",
        )
        return {
            "status": "unresolved",
            "event_id": event_id,
            "provider_dispute_id": dispute.id,
        }

    try:
        normalized = normalize_with_enrichment(envelope, event_id)
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
            failure_reason=str(exc),
        )
        raise

    store.update_provider_event(
        event_id,
        processing_state=result["status"],
        merchant_id=merchant["merchant_id"],
    )
    if result["status"] == "scheduled":
        return JSONResponse(status_code=202, content=result)
    return result
