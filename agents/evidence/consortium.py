import logging

from core.state import ChargebackState, ConsortiumEvidence


logger = logging.getLogger(__name__)


def consortium_agent(state: ChargebackState) -> ChargebackState:
    """Collect network and consortium dispute intelligence."""
    logger.info("Running consortium evidence agent for %s", state["chargeback_id"])

    evidence: ConsortiumEvidence = {
        "ethoca_match": False,
        "verifi_match": False,
        "cross_merchant_fraud_history": False,
        "dispute_count_across_merchants": 0,
        "raw": {
            "source": "consortium_agent_stub",
            "card_network": state["card_network"],
        },
    }
    state["consortium"] = evidence
    return state
