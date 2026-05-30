import logging
from datetime import datetime, timezone
from pathlib import Path

from core.state import ChargebackState


logger = logging.getLogger(__name__)


def _confirmation_id(state: ChargebackState, filed_at: datetime) -> str:
    timestamp = filed_at.strftime("%Y%m%d%H%M%S")
    return f"filed_{state['card_network'].lower()}_{state['chargeback_id']}_{timestamp}"


def filing_agent(state: ChargebackState) -> ChargebackState:
    """Record a filing confirmation for the prepared rebuttal."""
    logger.info("Running filing agent for %s", state["chargeback_id"])

    if not state.get("quality_approved"):
        state["filing_confirmation"] = "filing_blocked_quality_not_approved"
        return state

    path = state.get("rebuttal_document_path")
    if not path or not Path(path).exists():
        state["filing_confirmation"] = "filing_blocked_missing_rebuttal"
        return state

    filed_at = datetime.now(timezone.utc)
    state["filed_at"] = filed_at
    state["filing_confirmation"] = _confirmation_id(state, filed_at)
    return state
