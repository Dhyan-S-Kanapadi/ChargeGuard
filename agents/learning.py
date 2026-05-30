import logging
from datetime import datetime, timezone

from core.state import ChargebackState


logger = logging.getLogger(__name__)


def _default_outcome_reason(state: ChargebackState) -> str:
    outcome = state.get("final_outcome")
    if outcome == "WIN":
        return "Representment won; evidence and scoring signals should be reinforced."
    if outcome == "LOSS":
        return "Representment lost; evidence and scoring signals should be reviewed."
    return "Awaiting network decision."


def learning_agent(state: ChargebackState) -> ChargebackState:
    """Capture a pending outcome record for future feedback learning."""
    logger.info("Running learning agent for %s", state["chargeback_id"])

    if state.get("final_outcome") is None:
        state["final_outcome"] = "PENDING"
    if state.get("outcome_reason") is None:
        state["outcome_reason"] = _default_outcome_reason(state)
    state["outcome_recorded_at"] = datetime.now(timezone.utc)
    return state
