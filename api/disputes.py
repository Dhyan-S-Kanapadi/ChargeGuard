from copy import deepcopy
import os
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from agents.learning import learning_agent
from api.auth import require_api_key
from api.schemas import DisputeDetail, DisputeSummary, OutcomeResponse, OutcomeUpdate
from api.store import store
from core.state import is_filed_dispute


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
    return DisputeDetail(
        **record,
        win_probability=state.get("win_probability"),
        expected_value=state.get("expected_value"),
        third_party_fraud_indicators=state.get("third_party_fraud_indicators"),
        identity_continuity=state.get("identity_continuity"),
    )


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
    if state.get("final_outcome") in {"WIN", "LOSS"}:
        raise HTTPException(status_code=409, detail="Final outcome already recorded.")
    if not is_filed_dispute(state):
        raise HTTPException(
            status_code=409,
            detail="Only filed representment disputes can record adjudicated outcomes.",
        )
    state["final_outcome"] = payload.outcome
    state["outcome_reason"] = payload.reason
    state = learning_agent(state)
    store.update_dispute(chargeback_id, status="completed", state=state)
    return OutcomeResponse(
        chargeback_id=chargeback_id,
        final_outcome=state["final_outcome"],
        outcome_reason=state["outcome_reason"],
        outcome_recorded_at=state["outcome_recorded_at"],
    )
