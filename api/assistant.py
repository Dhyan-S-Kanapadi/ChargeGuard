"""Read-only, grounded assistant endpoint for portfolio questions."""

import logging
import math
import os
from threading import Lock
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from api.auth import require_api_key
from api.disputes import _redact_state
from api.schemas import AssistantQuery, AssistantResponse
from api.stats import build_stats
from api.store import store
from integrations.portfolio_assistant import (
    PortfolioAssistantClient, PortfolioAssistantConfigError,
    generate_portfolio_answer, portfolio_assistant_uses_stubs,
)
from integrations.decision_review import (
    DecisionReviewClient, DecisionReviewConfigError,
    decision_review_enabled, decision_review_uses_stubs,
)


logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/assistant",
    tags=["assistant"],
    dependencies=[Depends(require_api_key)],
)
_RATE_LIMIT_LOCK = Lock()
_RATE_LIMIT_BUCKETS: dict[str, tuple[float, float]] = {}


def _rate_limit_per_minute() -> int:
    try:
        return max(1, int(os.getenv("ASSISTANT_RATE_LIMIT_PER_MINUTE", "10")))
    except ValueError:
        logger.warning("Invalid ASSISTANT_RATE_LIMIT_PER_MINUTE; using 10")
        return 10


def enforce_assistant_rate_limit(api_key: str = Depends(require_api_key)) -> None:
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
        detail="Assistant rate limit exceeded.",
        headers={"Retry-After": str(retry_after)},
    )


def reset_assistant_rate_limiter() -> None:
    """Reset process-local state for tests."""
    with _RATE_LIMIT_LOCK:
        _RATE_LIMIT_BUCKETS.clear()


def _summary_from_record(record: dict[str, Any]) -> dict[str, Any]:
    state = _redact_state(record["state"])
    deadline = state.get("filing_deadline")
    device = state.get("device") or {}
    return {
        "chargeback_id": record["chargeback_id"],
        "decision": state.get("decision"),
        "win_probability": state.get("win_probability"),
        "expected_value": state.get("expected_value"),
        "final_outcome": state.get("final_outcome"),
        "contradiction_summary": state.get("contradiction_summary"),
        "decision_reasoning": str(state.get("decision_reasoning") or "")[:600],
        "filing_deadline": deadline.isoformat() if hasattr(deadline, "isoformat") else deadline,
        "dispute_amount": state.get("dispute_amount"),
        "currency": state.get("currency"),
        "synthetic": record["chargeback_id"].startswith("disp_SIM_"),
        "degraded_reasons": [str(reason)[:100] for reason in state.get("degraded_reasons", [])[:12]],
        "device_risk": {key: device.get(key) for key in ("fraud_score", "vpn_detected", "geolocation_match")},
    }


def build_assistant_context(chargeback_id: str | None = None) -> dict[str, Any]:
    records = store.list_disputes()
    selected: list[dict[str, Any]] = []
    requested_record = None
    if chargeback_id:
        requested_record = next(
            (record for record in records if record["chargeback_id"] == chargeback_id),
            None,
        )
        if requested_record is not None:
            selected.append(requested_record)
    for record in records:
        if len(selected) >= 20 or requested_record is not None:
            break
        if record is not requested_record:
            selected.append(record)
    context = {
        "stats": build_stats(records),
        "disputes": [_summary_from_record(record) for record in selected[:20]],
    }
    if chargeback_id:
        context["requested_chargeback_id"] = chargeback_id
        context["requested_chargeback_found"] = requested_record is not None
    return context


@router.post("/query", response_model=AssistantResponse)
def query_assistant(
    payload: AssistantQuery,
    _: None = Depends(enforce_assistant_rate_limit),
) -> AssistantResponse:
    context = build_assistant_context(payload.chargeback_id)
    try:
        answer = generate_portfolio_answer(payload.question, context)
    except Exception as exc:
        logger.warning("Portfolio assistant generation failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Portfolio assistant is unavailable.") from exc
    return AssistantResponse(
        answer=answer,
        based_on={"dispute_count": len(context["disputes"]), "stats_snapshot": True},
    )


@router.get("/status")
def llm_status() -> dict[str, dict[str, str | None]]:
    """Authenticated configuration status; does not send a billable provider request."""
    if portfolio_assistant_uses_stubs():
        chat = {"mode": "stub", "model": None}
    else:
        try:
            client = PortfolioAssistantClient.from_env()
            chat = {"mode": "live_configured", "model": client.model}
        except PortfolioAssistantConfigError:
            chat = {"mode": "unavailable", "model": None}
    if not decision_review_enabled():
        review = {"mode": "disabled", "model": None}
    elif decision_review_uses_stubs():
        review = {"mode": "stub", "model": "decision-review-stub-v1"}
    else:
        try:
            client = DecisionReviewClient.from_env()
            review = {"mode": "live_configured", "model": client.model}
        except DecisionReviewConfigError:
            review = {"mode": "unavailable", "model": None}
    return {"guard_ai": chat, "decision_review": review}
