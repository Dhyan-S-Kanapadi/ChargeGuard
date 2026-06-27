import logging
from datetime import datetime, timezone

from core.state import ChargebackState
from ml.feedback import record_outcome


logger = logging.getLogger(__name__)


def _default_outcome_reason(state: ChargebackState) -> str:
    outcome = state.get("final_outcome")
    if outcome == "WIN":
        return "Representment won; evidence and scoring signals should be reinforced."
    if outcome == "LOSS":
        return "Representment lost; evidence and scoring signals should be reviewed."
    return "Awaiting network decision."


def learning_agent(state: ChargebackState) -> ChargebackState:
    """Record feedback only after a terminal WIN or LOSS outcome."""
    logger.info("Running learning agent for %s", state["chargeback_id"])

    if state.get("final_outcome") not in {"WIN", "LOSS"}:
        logger.info("Skipping learning for non-terminal chargeback %s", state["chargeback_id"])
        return state
    if state.get("outcome_reason") is None:
        state["outcome_reason"] = _default_outcome_reason(state)
    state["outcome_recorded_at"] = datetime.now(timezone.utc)
    try:
        result = record_outcome(state)
        logger.info("Recorded learning feedback for %s: %s", state["chargeback_id"], result)
    except Exception:
        logger.exception("Unable to persist learning feedback for %s", state["chargeback_id"])
    return state
