from typing import Any

from fastapi import APIRouter, Depends

from api.auth import require_api_key
from api.store import store
from core.state import is_filed_dispute


router = APIRouter(
    prefix="/stats",
    tags=["stats"],
    dependencies=[Depends(require_api_key)],
)


@router.get("")
def get_stats() -> dict[str, Any]:
    records = store.list_disputes()
    decisions = {"FIGHT": 0, "ACCEPT": 0, "ESCALATE_DEGRADED": 0}
    expected_values: list[float] = []
    filed_outcomes: list[str] = []
    degraded_count = 0

    for record in records:
        state = record["state"]
        decision = state.get("decision")
        if decision in decisions:
            decisions[decision] += 1
        if state.get("expected_value") is not None:
            expected_values.append(float(state["expected_value"]))
        if state.get("evidence_collection_degraded"):
            degraded_count += 1
        if is_filed_dispute(state) and state.get("final_outcome") in {"WIN", "LOSS"}:
            filed_outcomes.append(state["final_outcome"])

    return {
        "total_disputes_processed": len(records),
        "decisions": decisions,
        "win_rate": (
            sum(outcome == "WIN" for outcome in filed_outcomes) / len(filed_outcomes)
            if filed_outcomes
            else None
        ),
        "average_expected_value": (
            sum(expected_values) / len(expected_values) if expected_values else None
        ),
        "evidence_collection_degraded_count": degraded_count,
    }
