from copy import deepcopy
import os
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from analytics.merchant_stats import merchant_dispute_ratio
from api.auth import require_api_key
from api.schemas import (
    CaseSummaryResponse,
    DisputeDetail,
    DisputeSummary,
    OutcomeResponse,
    OutcomeUpdate,
)
from api.store import store
from core.outcomes import (
    OutcomeConflictError,
    OutcomeNotEligibleError,
    record_adjudicated_outcome,
)
from integrations.case_summary import generate_case_summary


router = APIRouter(
    prefix="/disputes",
    tags=["disputes"],
    dependencies=[Depends(require_api_key)],
)


_EVIDENCE_KEYS = (
    "transaction",
    "shipping",
    "comms",
    "device",
    "consortium",
    "delivery_photo",
    "order_timeline",
)
_TRANSACTION_PII_KEYS = ("customer_email", "ip_address", "device_id")
_MERCHANT_SECRET_KEYS = (
    "shopify_admin_api_token",
    "woocommerce_api_key",
    "woocommerce_api_secret",
)


def _redact_state(state: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(state)
    for key in _EVIDENCE_KEYS:
        evidence = redacted.get(key)
        if isinstance(evidence, dict):
            evidence.pop("raw", None)

    transaction = redacted.get("transaction")
    if isinstance(transaction, dict):
        for key in _TRANSACTION_PII_KEYS:
            transaction.pop(key, None)

    merchant = redacted.get("merchant_profile")
    if isinstance(merchant, dict):
        for key in _MERCHANT_SECRET_KEYS:
            merchant.pop(key, None)
    redacted.pop("disputed_order_ip", None)
    redacted.pop("disputed_order_user_agent", None)

    return redacted


def _authorize_raw_access(include_raw: bool, internal_token: str | None) -> None:
    if not include_raw:
        return

    expected_token = os.getenv("INTERNAL_API_TOKEN")
    if not expected_token or internal_token != expected_token:
        raise HTTPException(status_code=403, detail="Internal token required.")


@router.get("", response_model=list[DisputeSummary])
def list_disputes() -> list[DisputeSummary]:
    summaries: list[DisputeSummary] = []
    for record in store.list_disputes():
        state = _redact_state(record["state"])
        summaries.append(
            DisputeSummary(
                chargeback_id=record["chargeback_id"],
                status=record["status"],
                decision=state.get("decision"),
                dispute_amount=state["dispute_amount"],
                currency=state["currency"],
                merchant_id=state["merchant_profile"]["merchant_id"],
                created_at=record["created_at"],
                updated_at=record["updated_at"],
            )
        )
    return summaries


@router.get("/{chargeback_id}", response_model=DisputeDetail)
def get_dispute(
    chargeback_id: str,
    include_raw: bool = Query(default=False),
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> DisputeDetail:
    _authorize_raw_access(include_raw, x_internal_token)
    record = store.get_dispute(chargeback_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Dispute not found.")
    if not include_raw:
        record["state"] = _redact_state(record["state"])
    state = record["state"]
    merchant = state["merchant_profile"]
    return DisputeDetail(
        **record,
        win_probability=state.get("win_probability"),
        expected_value=state.get("expected_value"),
        third_party_fraud_indicators=state.get("third_party_fraud_indicators"),
        identity_continuity=state.get("identity_continuity"),
        human_review_summary=state.get("human_review_summary"),
        merchant_dispute_ratio=(
            merchant_dispute_ratio(
                merchant,
                store.list_disputes(),
                state["card_network"],
            )
            if state.get("card_network")
            else None
        ),
    )


@router.get("/{chargeback_id}/summary", response_model=CaseSummaryResponse)
def get_dispute_summary(chargeback_id: str) -> CaseSummaryResponse:
    record = store.get_dispute(chargeback_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Dispute not found.")
    try:
        summary = generate_case_summary(record["state"])
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Case summary generation is unavailable.") from exc
    return CaseSummaryResponse(chargeback_id=chargeback_id, human_review_summary=summary)


@router.post("/{chargeback_id}/outcome", response_model=OutcomeResponse)
def record_dispute_outcome(
    chargeback_id: str,
    payload: OutcomeUpdate,
) -> OutcomeResponse:
    record = store.get_dispute(chargeback_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Dispute not found.")
    if record["status"] != "completed":
        raise HTTPException(status_code=409, detail="Dispute processing is not complete.")

    state = record["state"]
    try:
        state, _ = record_adjudicated_outcome(
            state,
            payload.outcome,
            payload.reason,
        )
    except (OutcomeConflictError, OutcomeNotEligibleError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    store.update_dispute(chargeback_id, status="completed", state=state)
    return OutcomeResponse(
        chargeback_id=chargeback_id,
        final_outcome=state["final_outcome"],
        outcome_reason=state["outcome_reason"],
        outcome_recorded_at=state["outcome_recorded_at"],
    )
