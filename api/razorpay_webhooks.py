"""Production-compatible Razorpay dispute webhook receiver."""

from datetime import datetime, timezone
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from fastapi.responses import JSONResponse

from agents.learning import learning_agent
from api.schemas import ChargebackWebhookPayload
from api.store import store
from api.webhooks import create_and_schedule_dispute
from core.state import is_filed_dispute
from integrations.razorpay_webhook import (
    RazorpayWebhookError,
    mapped_values,
    parse_envelope,
    payload_sha256,
    verify_signature,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhook", tags=["razorpay-webhooks"])
_SUPPORTED_EVENTS = {
    "payment.dispute.created",
    "payment.dispute.action_required",
    "payment.dispute.under_review",
    "payment.dispute.won",
    "payment.dispute.lost",
    "payment.dispute.closed",
}
_CREATE_EVENTS = {"payment.dispute.created", "payment.dispute.action_required"}


def _event_metadata(event_id: str, event_name: str, account_id: str, raw_body: bytes, values: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider_event_id": event_id,
        "provider": "razorpay",
        "event_name": event_name,
        "account_id": account_id,
        "chargeback_id": values.get("chargeback_id"),
        "payment_id": values.get("payment_id"),
        "payload_sha256": payload_sha256(raw_body),
        "processing_status": "received",
    }


def _mapped_payload(values: dict[str, Any], merchant_id: str) -> ChargebackWebhookPayload | None:
    network = values.get("card_network")
    reason = values.get("network_reason_code")
    deadline = values.get("filing_deadline")
    if network not in {"VISA", "MASTERCARD", "RUPAY", "AMEX"} or not reason or not deadline:
        return None
    if not values.get("payment_id") or not values.get("order_id") or not values.get("currency"):
        return None
    return ChargebackWebhookPayload(
        chargeback_id=values["chargeback_id"],
        reason_code=str(reason),
        card_network=network,
        dispute_amount=values["dispute_amount"],
        currency=values["currency"],
        filing_deadline=deadline,
        merchant_id=merchant_id,
        order_id=values["order_id"],
        payment_id=values["payment_id"],
    )


def _apply_provider_metadata(state, *, event_id: str, account_id: str, values: dict[str, Any]) -> None:
    state["provider"] = "razorpay"
    state["provider_event_id"] = event_id
    state["provider_account_id"] = account_id
    state["provider_dispute_status"] = str(values.get("provider_dispute_status") or "")
    state["provider_phase"] = str(values.get("provider_phase") or "")
    state["provider_reason_code"] = str(values.get("provider_reason_code") or "")
    if values.get("provider_respond_by") is not None:
        state["provider_respond_by"] = values["provider_respond_by"]


def _record_terminal_outcome(chargeback_id: str, outcome: str, values: dict[str, Any]) -> None:
    record = store.get_dispute(chargeback_id)
    if record is None:
        return
    state = record["state"]
    state["provider_dispute_status"] = str(values.get("provider_dispute_status") or outcome.lower())
    if state.get("final_outcome") not in {"WIN", "LOSS"} and is_filed_dispute(state):
        state["final_outcome"] = outcome
        state["outcome_reason"] = f"Razorpay dispute marked {outcome.lower()}."
        state = learning_agent(state)
    store.update_dispute(chargeback_id, status=record["status"], state=state, error=record.get("error"))


@router.post("/razorpay")
async def receive_razorpay_webhook(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature")
    if not verify_signature(raw_body, signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Razorpay signature.")
    event_id = request.headers.get("x-razorpay-event-id")
    if not event_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing Razorpay event ID.")
    try:
        envelope = parse_envelope(raw_body)
        event_name = str(envelope.get("event") or "")
        account_id = str(envelope.get("account_id") or "")
    except RazorpayWebhookError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    if event_name not in _SUPPORTED_EVENTS:
        event = {
            "provider_event_id": event_id,
            "provider": "razorpay",
            "event_name": event_name,
            "account_id": account_id,
            "chargeback_id": None,
            "payment_id": None,
            "payload_sha256": payload_sha256(raw_body),
            "processing_status": "ignored",
        }
        if not store.claim_provider_event(event):
            return {"status": "duplicate", "event_id": event_id}
        return {"status": "ignored", "event_id": event_id}
    try:
        values = mapped_values(envelope)
    except RazorpayWebhookError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    if not store.claim_provider_event(_event_metadata(event_id, event_name, account_id, raw_body, values)):
        return {"status": "duplicate", "event_id": event_id}
    merchant = store.get_merchant_by_razorpay_account_id(account_id)
    if merchant is None:
        store.update_provider_event(event_id, processing_status="unknown_merchant", error="Unknown Razorpay account")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown Razorpay account ID.")
    payload = _mapped_payload(values, merchant["merchant_id"])
    if payload is None:
        store.update_provider_event(event_id, processing_status="unmapped", error="Missing network mapping")
        return {"status": "unmapped", "event_id": event_id}

    existing = store.get_dispute(payload.chargeback_id)
    if event_name in _CREATE_EVENTS and existing is None:
        state, created = create_and_schedule_dispute(payload, merchant, background_tasks)
        _apply_provider_metadata(state, event_id=event_id, account_id=account_id, values=values)
        store.update_dispute(payload.chargeback_id, status="received", state=state)
        store.update_provider_event(event_id, processing_status="scheduled" if created else "duplicate_dispute")
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"status": "scheduled", "chargeback_id": payload.chargeback_id},
        )
    if existing is not None:
        state = existing["state"]
        _apply_provider_metadata(state, event_id=event_id, account_id=account_id, values=values)
        store.update_dispute(payload.chargeback_id, status=existing["status"], state=state, error=existing.get("error"))
    if event_name == "payment.dispute.won":
        _record_terminal_outcome(payload.chargeback_id, "WIN", values)
    elif event_name == "payment.dispute.lost":
        _record_terminal_outcome(payload.chargeback_id, "LOSS", values)
    store.update_provider_event(event_id, processing_status="updated")
    return {"status": "updated", "chargeback_id": payload.chargeback_id}
