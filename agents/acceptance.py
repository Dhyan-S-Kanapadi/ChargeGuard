import logging

from core.state import ChargebackState


logger = logging.getLogger(__name__)


def accept_and_log_agent(state: ChargebackState) -> ChargebackState:
    """Accept low-value or low-probability disputes without filing a rebuttal."""
    logger.info("Accepting chargeback %s without representment", state["chargeback_id"])

    state["decision"] = "ACCEPT"
    state["filing_confirmation"] = "accepted_no_filing"
    state["final_outcome"] = "ACCEPTED_NO_CONTEST"
    if state.get("outcome_reason") is None:
        state["outcome_reason"] = "Chargeback accepted because representment was not economically justified."
    return state
