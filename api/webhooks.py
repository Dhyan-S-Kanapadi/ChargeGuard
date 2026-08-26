import logging
import math
import os
from threading import Lock
import time
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from api.auth import require_api_key
from api.schemas import ChargebackWebhookPayload, WebhookAccepted
from api.store import store
from core.graph import app as chargeback_graph
from core.state import ChargebackState


logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/webhook",
    tags=["webhooks"],
    dependencies=[Depends(require_api_key)],
)
_RATE_LIMIT_LOCK = Lock()
_RATE_LIMIT_BUCKETS: dict[str, tuple[float, float]] = {}


def _rate_limit_per_minute() -> int:
    try:
        return max(1, int(os.getenv("WEBHOOK_RATE_LIMIT_PER_MINUTE", "30")))
    except ValueError:
        logger.warning("Invalid WEBHOOK_RATE_LIMIT_PER_MINUTE; using 30")
        return 30


def enforce_webhook_rate_limit(api_key: str = Depends(require_api_key)) -> None:
    limit = _rate_limit_per_minute()
    now = time.monotonic()
    refill_per_second = limit / 60
    with _RATE_LIMIT_LOCK:
        tokens, last_updated = _RATE_LIMIT_BUCKETS.get(api_key, (float(limit), now))
        tokens = min(float(limit), tokens + ((now - last_updated) * refill_per_second))
        if tokens >= 1:
            _RATE_LIMIT_BUCKETS[api_key] = (tokens - 1, now)
            return
        retry_after = max(1, math.ceil((1 - tokens) / refill_per_second))
        _RATE_LIMIT_BUCKETS[api_key] = (tokens, now)

    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Webhook rate limit exceeded.",
        headers={"Retry-After": str(retry_after)},
    )


def reset_webhook_rate_limiter() -> None:
    """Reset process-local state for tests."""
    with _RATE_LIMIT_LOCK:
        _RATE_LIMIT_BUCKETS.clear()


def run_chargeback_graph(state: ChargebackState) -> None:
    chargeback_id = state["chargeback_id"]
    store.update_dispute(chargeback_id, status="processing")
    try:
        result = chargeback_graph.invoke(state)
        store.update_dispute(chargeback_id, status="completed", state=result)
    except Exception as exc:
        logger.exception("Chargeback graph failed for %s", chargeback_id)
        store.update_dispute(chargeback_id, status="failed", state=state, error=str(exc))


def _initial_state(
    payload: ChargebackWebhookPayload,
    merchant_profile,
) -> ChargebackState:
    state: ChargebackState = {
        "chargeback_id": payload.chargeback_id,
        "order_id": payload.order_id,
        "payment_id": payload.payment_id,
        "reason_code": payload.reason_code,
        "card_network": payload.card_network,
        "dispute_amount": payload.dispute_amount,
        "currency": payload.currency,
        "filing_deadline": payload.filing_deadline,
        "chargeback_received_at": datetime.now(timezone.utc),
        "merchant_profile": merchant_profile,
        "investigation_plan": {},
        "requires_food_agents": False,
        "transaction": None,
        "shipping": None,
        "comms": None,
        "device": None,
        "consortium": None,
        "delivery_photo": None,
        "order_timeline": None,
        "evidence_collection_degraded": payload.simulate_evidence_degraded,
        "degraded_reasons": (
            ["demo_simulation"] if payload.simulate_evidence_degraded else []
        ),
        "win_probability": None,
        "expected_value": None,
        "decision": None,
        "decision_reasoning": None,
        "rebuttal_document_path": None,
        "quality_approved": False,
        "quality_rejection_reason": None,
        "quality_loop_count": 0,
        "filing_confirmation": None,
        "filed_at": None,
        "final_outcome": None,
        "outcome_reason": None,
        "outcome_recorded_at": None,
    }
    if payload.tracking_id:
        state["tracking_id"] = payload.tracking_id
    if payload.card_fingerprint:
        state["card_fingerprint"] = payload.card_fingerprint
    return state


@router.post(
    "/chargeback",
    response_model=WebhookAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def receive_chargeback(
    payload: ChargebackWebhookPayload,
    background_tasks: BackgroundTasks,
    _: None = Depends(enforce_webhook_rate_limit),
) -> WebhookAccepted:
    merchant = store.get_merchant(payload.merchant_id)
    if merchant is None:
        raise HTTPException(status_code=404, detail="Merchant not found.")

    state = _initial_state(payload, merchant)
    if not store.create_dispute(state):
        raise HTTPException(status_code=409, detail="Chargeback already exists.")
    background_tasks.add_task(run_chargeback_graph, state)
    return WebhookAccepted(status="received", chargeback_id=payload.chargeback_id)
