import logging

from core.state import ChargebackState


logger = logging.getLogger(__name__)


def _decision_log_extra(state: ChargebackState) -> dict[str, object]:
    return {
        "chargeback_id": state["chargeback_id"],
        "decision": state.get("decision"),
        "win_probability": state.get("win_probability"),
        "expected_value": state.get("expected_value"),
        "dispute_amount": state["dispute_amount"],
        "currency": state["currency"],
    }


def human_escalation_agent(state: ChargebackState) -> ChargebackState:
    """Escalate cases that fail repeated automated quality checks."""
    state["filing_confirmation"] = "human_review_required"
    state["final_outcome"] = "PENDING"
    if state.get("outcome_reason") is None:
        rejection = state.get("quality_rejection_reason")
        if rejection:
            state["outcome_reason"] = f"Human review required after automated quality rejection: {rejection}"
        else:
            state["outcome_reason"] = "Human review required before filing."
    logger.info(
        "Escalating chargeback %s for human review",
        state["chargeback_id"],
        extra=_decision_log_extra(state),
    )
    return state
