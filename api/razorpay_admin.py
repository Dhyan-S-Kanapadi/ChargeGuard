"""Protected Razorpay operations for reconciliation and event remediation."""

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from api import webhooks as internal_webhooks
from api.auth import require_api_key
from api.razorpay_service import process_normalized_dispute
from api.schemas import RazorpayReconciliationRequest
from api.store import store
from integrations.razorpay import (
    RazorpayClient,
    RazorpayConfigError,
    RazorpayRequestError,
)
from integrations.razorpay_schemas import RazorpayWebhookEnvelope
from integrations.razorpay_webhook import normalize_dispute


router = APIRouter(
    prefix="/internal/razorpay",
    tags=["razorpay-internal"],
    dependencies=[Depends(require_api_key)],
)


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
    return events


@router.post("/reconcile")
def reconcile_razorpay_disputes(
    payload: RazorpayReconciliationRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    merchant = store.get_merchant(payload.merchant_id)
    if merchant is None:
        raise HTTPException(status_code=404, detail="Merchant not found.")
    account_id = merchant.get("razorpay_account_id")
    if merchant.get("payment_provider") != "razorpay" or not account_id:
        raise HTTPException(
            status_code=422,
            detail="Merchant must have a Razorpay account mapping.",
        )

    try:
        client = RazorpayClient.from_env()
        disputes = client.list_disputes(
            from_timestamp=payload.from_timestamp,
            to_timestamp=payload.to_timestamp,
            count=payload.count,
            skip=payload.skip,
        )
    except (RazorpayConfigError, RazorpayRequestError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
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
                "processing_state": "processing",
                "received_at": datetime.now(timezone.utc),
            }
        )
        if not claimed:
            results.append({"status": "duplicate", "provider_dispute_id": dispute_id})
            continue
        try:
            envelope = _reconciliation_envelope(dispute, payment, account_id)
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
                failure_reason=str(exc),
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
