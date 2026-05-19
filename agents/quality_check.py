import logging
from pathlib import Path

from core.state import ChargebackState


logger = logging.getLogger(__name__)


def quality_check_agent(state: ChargebackState) -> ChargebackState:
    """Validate that the dispute packet has the minimum evidence to file."""
    logger.info("Running quality check agent for %s", state["chargeback_id"])

    state["quality_loop_count"] = state.get("quality_loop_count", 0) + 1

    path = state.get("rebuttal_document_path")
    if not path or not Path(path).exists():
        state["quality_approved"] = False
        state["quality_rejection_reason"] = "Missing rebuttal document."
        return state

    required_evidence = ["transaction", "shipping"]
    missing = [name for name in required_evidence if not state.get(name)]
    if missing:
        state["quality_approved"] = False
        state["quality_rejection_reason"] = f"Missing required evidence: {', '.join(missing)}."
        return state

    state["quality_approved"] = True
    state["quality_rejection_reason"] = None
    return state
