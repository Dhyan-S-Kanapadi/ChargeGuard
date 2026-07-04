from fastapi import APIRouter, HTTPException

from agents.learning import learning_agent
from api.schemas import DisputeDetail, DisputeSummary, OutcomeResponse, OutcomeUpdate
from api.store import store


router = APIRouter(prefix="/disputes", tags=["disputes"])


@router.get("", response_model=list[DisputeSummary])
def list_disputes() -> list[DisputeSummary]:
    summaries: list[DisputeSummary] = []
    for record in store.list_disputes():
        state = record["state"]
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
def get_dispute(chargeback_id: str) -> DisputeDetail:
    record = store.get_dispute(chargeback_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Dispute not found.")
    return DisputeDetail(**record)


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
