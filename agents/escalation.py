import logging

from core.state import ChargebackState


logger = logging.getLogger(__name__)


def human_escalation_agent(state: ChargebackState) -> ChargebackState:
    """Escalate cases that fail repeated automated quality checks."""
    logger.info("Escalating chargeback %s for human review", state["chargeback_id"])

    state["filing_confirmation"] = "human_review_required"
    state["final_outcome"] = "PENDING"
    if state.get("outcome_reason") is None:
        rejection = state.get("quality_rejection_reason")
        if rejection:
            state["outcome_reason"] = f"Human review required after automated quality rejection: {rejection}"
        else:
            state["outcome_reason"] = "Human review required before filing."
    return state
