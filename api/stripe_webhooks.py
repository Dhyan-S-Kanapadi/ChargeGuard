"""Signed Stripe dispute webhook receiver using the shared provider-event store."""

import hashlib
import os
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from fastapi.responses import JSONResponse

from api.store import store
from api.stripe_processor import process_stripe_provider_event
from integrations.stripe_webhook import StripeWebhookError, event_timestamp, parse_event, serialize_event, verify_signature

router = APIRouter(prefix="/webhook", tags=["stripe-webhooks"])
SUPPORTED_EVENTS = {"charge.dispute.created", "charge.dispute.updated", "charge.dispute.closed", "charge.dispute.funds_reinstated", "charge.dispute.funds_withdrawn"}


def _max_body_bytes() -> int:
    try: return max(1024, int(os.getenv("STRIPE_WEBHOOK_MAX_BODY_BYTES", "1048576")))
    except ValueError: return 1048576


@router.post("/stripe")
async def receive_stripe_webhook(request: Request, background_tasks: BackgroundTasks):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _max_body_bytes(): raise HTTPException(413, "Stripe webhook body is too large.")
        except ValueError: raise HTTPException(400, "Invalid Content-Length header.")
    raw_body = await request.body()
    if len(raw_body) > _max_body_bytes(): raise HTTPException(413, "Stripe webhook body is too large.")
    if not verify_signature(raw_body, request.headers.get("Stripe-Signature")):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid Stripe signature.")
    try: event = parse_event(raw_body)
    except StripeWebhookError as exc: raise HTTPException(422, str(exc)) from exc
    safe = serialize_event(event); dispute = safe["data"]["object"]
    claimed = store.claim_provider_event({"event_id": event["id"], "provider": "stripe", "event_type": event["type"], "provider_dispute_id": dispute.get("id"), "payment_id": dispute.get("payment_intent") or dispute.get("charge"), "account_id": safe.get("account"), "payload_hash": hashlib.sha256(raw_body).hexdigest(), "payload_sha256": hashlib.sha256(raw_body).hexdigest(), "event_id_source": "stripe_event_id", "provider_event_timestamp": event_timestamp(event), "event_data": safe, "processing_state": "received"})
    if not claimed: return {"status": "duplicate", "event_id": event["id"]}
    if event["type"] not in SUPPORTED_EVENTS:
        store.update_provider_event(event["id"], processing_state="ignored")
        return {"status": "ignored", "event_id": event["id"]}
    store.queue_provider_event(event["id"])
    background_tasks.add_task(process_stripe_provider_event, event["id"])
    return JSONResponse(status_code=202, content={"status": "queued", "event_id": event["id"]})
