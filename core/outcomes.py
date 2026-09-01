from typing import Literal

from agents.learning import learning_agent
from core.state import ChargebackState, is_filed_dispute


class OutcomeConflictError(ValueError):
    """Raised when a terminal outcome conflicts with the stored outcome."""


class OutcomeNotEligibleError(ValueError):
    """Raised when an unfiled case is presented as an adjudicated outcome."""


def record_adjudicated_outcome(
    state: ChargebackState,
    outcome: Literal["WIN", "LOSS"],
    reason: str | None,
) -> tuple[ChargebackState, bool]:
    existing = state.get("final_outcome")
    if existing == outcome:
        return state, False
    if existing in {"WIN", "LOSS"}:
        raise OutcomeConflictError("Final outcome already recorded.")
    if not is_filed_dispute(state):
        raise OutcomeNotEligibleError(
            "Only filed representment disputes can record adjudicated outcomes."
        )
    state["final_outcome"] = outcome
    state["outcome_reason"] = reason
    return learning_agent(state), True
