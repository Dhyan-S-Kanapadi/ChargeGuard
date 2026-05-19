import logging
from datetime import datetime, timezone

from core.state import ChargebackState


logger = logging.getLogger(__name__)


def filing_agent(state: ChargebackState) -> ChargebackState:
    """Record a filing confirmation for the prepared rebuttal."""
    logger.info("Running filing agent for %s", state["chargeback_id"])

    filed_at = datetime.now(timezone.utc)
    state["filed_at"] = filed_at
    state["filing_confirmation"] = f"filed_{state['card_network'].lower()}_{state['chargeback_id']}"
    return state
