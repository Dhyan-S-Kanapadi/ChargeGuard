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


def accept_and_log_agent(state: ChargebackState) -> ChargebackState:
    """Accept low-value or low-probability disputes without filing a rebuttal."""
    state["decision"] = "ACCEPT"
    state["filing_confirmation"] = "accepted_no_filing"
    state["final_outcome"] = "ACCEPTED_NO_CONTEST"
    if state.get("outcome_reason") is None:
        state["outcome_reason"] = "Chargeback accepted because representment was not economically justified."
    logger.info(
        "Accepting chargeback without representment",
        extra=_decision_log_extra(state),
    )
    return state
