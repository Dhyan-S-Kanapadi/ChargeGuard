import logging
from typing import Any

from core.state import ChargebackState, ConsortiumEvidence


logger = logging.getLogger(__name__)


def _empty_consortium_evidence(
    state: ChargebackState,
    *,
    error: str | None = None,
) -> ConsortiumEvidence:
    return {
        "ethoca_match": False,
        "verifi_match": False,
        "cross_merchant_fraud_history": False,
        "dispute_count_across_merchants": 0,
        "raw": {
            "source": "consortium_agent_empty",
            "error": error,
            "card_network": state["card_network"],
        },
    }


def _stub_consortium_response(state: ChargebackState) -> dict[str, Any]:
    return {
        "ethoca": {
            "match": False,
        },
        "verifi": {
            "match": False,
        },
        "history": {
            "cross_merchant_fraud_history": False,
            "dispute_count_across_merchants": 0,
        },
        "card_network": state["card_network"],
    }


def _build_consortium_evidence(response: dict[str, Any]) -> ConsortiumEvidence:
    ethoca = response.get("ethoca") or {}
    verifi = response.get("verifi") or {}
    history = response.get("history") or {}
    dispute_count = int(
        history.get("dispute_count_across_merchants")
        or response.get("dispute_count_across_merchants")
        or 0
    )

    evidence: ConsortiumEvidence = {
        "ethoca_match": bool(ethoca.get("match") or response.get("ethoca_match")),
        "verifi_match": bool(verifi.get("match") or response.get("verifi_match")),
        "cross_merchant_fraud_history": bool(
            history.get("cross_merchant_fraud_history")
            or response.get("cross_merchant_fraud_history")
            or dispute_count > 1
        ),
        "dispute_count_across_merchants": dispute_count,
        "raw": {
            "source": "consortium_agent_stub",
            "response": response,
        },
    }
    return evidence


def consortium_agent(state: ChargebackState) -> ChargebackState:
    """Collect network and consortium dispute intelligence."""
    logger.info("Running consortium evidence agent for %s", state["chargeback_id"])

    try:
        response = _stub_consortium_response(state)
        state["consortium"] = _build_consortium_evidence(response)
    except Exception as exc:
        logger.exception("Consortium evidence collection failed")
        state["consortium"] = _empty_consortium_evidence(state, error=str(exc))

    return state
